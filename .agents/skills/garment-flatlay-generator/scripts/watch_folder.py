#!/usr/bin/env python3
"""Watch incoming-clothes and trigger one skill run per ready reference image."""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from garment_queue import (
    claim_next,
    complete_claim,
    fail_claim,
    initialize,
    peek_next,
    verify_png,
)


SKILL_NAME = "garment-flatlay-generator"
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def codex_executable() -> str:
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    if DEFAULT_CODEX.is_file():
        return str(DEFAULT_CODEX)
    raise FileNotFoundError("Could not find the Codex executable")


@contextmanager
def single_instance(project_root: Path) -> Iterator[None]:
    lock_path = project_root / ".garment-flatlay-watcher.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"A watcher is already running for {project_root}") from exc
        yield
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def watcher_prompt(claim: dict[str, str]) -> str:
    return (
        f"Use ${SKILL_NAME} to process exactly the attached garment reference image. "
        f"The existing claim key is {claim['key']}. "
        f"The absolute input path is {claim['input']}. "
        f"The absolute output path is {claim['output']}. "
        "Invoke image generation exactly once, pass the bundled fixed image prompt unchanged, "
        "save without overwriting, verify the PNG, move the successful source to processed-inputs, complete the claim, and report the final path."
    )


def run_claim(
    project_root: Path,
    claim: dict[str, str],
    *,
    nested_sandbox_bypass: bool = False,
) -> bool:
    command = [
        codex_executable(),
        "exec",
        watcher_prompt(claim),
        "--cd",
        str(project_root),
    ]
    if nested_sandbox_bypass:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(["--sandbox", "workspace-write"])
    command.extend(
        [
            "--skip-git-repo-check",
            "--ephemeral",
            "--image",
            claim["input"],
        ]
    )
    print(
        json.dumps(
            {
                "status": "starting",
                "input": claim["input"],
                "output": claim["output"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    completed = subprocess.run(command, cwd=project_root, text=True, check=False)
    output_path = Path(claim["output"])
    try:
        verify_png(output_path)
        complete_claim(project_root, claim["key"], output_path)
        print(
            json.dumps(
                {"status": "complete", "output": str(output_path)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return True
    except Exception as exc:
        message = f"Codex exited {completed.returncode}; output verification failed: {exc}"
        try:
            fail_claim(project_root, claim["key"], message)
        except Exception:
            pass
        print(
            json.dumps(
                {"status": "failed", "input": claim["input"], "error": message},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=default_project_root())
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--nested-sandbox-bypass",
        action="store_true",
        help="Use only when this watcher is already confined by an external sandbox.",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    initialize(project_root)
    if args.dry_run:
        print(json.dumps(peek_next(project_root, args.retry_failed), ensure_ascii=False))
        return 0

    try:
        with single_instance(project_root):
            while True:
                claim = claim_next(project_root, args.retry_failed)
                if claim["status"] == "claimed":
                    succeeded = run_claim(
                        project_root,
                        claim,
                        nested_sandbox_bypass=args.nested_sandbox_bypass,
                    )
                    if args.once:
                        return 0 if succeeded else 1
                    continue
                if claim["status"] == "blocked":
                    print(json.dumps(claim, ensure_ascii=False), file=sys.stderr, flush=True)
                    if args.once:
                        return 2
                    continue
                if args.once:
                    print(json.dumps(claim, ensure_ascii=False))
                    return 0
                time.sleep(max(0.25, args.poll_seconds))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
