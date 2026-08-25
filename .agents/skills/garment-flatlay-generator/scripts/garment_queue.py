#!/usr/bin/env python3
"""Deterministic queue and state handling for garment flat-lay generation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_CONFIG: dict[str, Any] = {
    "input_dir": "incoming-clothes",
    "output_dir": "generated-images",
    "processed_dir": "processed-inputs",
    "output_pattern": "{stem}.png",
    "state_file": ".garment-flatlay-state.json",
    "settle_seconds": 3,
    "supported_extensions": [".jpg", ".jpeg", ".png", ".webp"],
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_path(root: Path, relative_value: str) -> Path:
    candidate = Path(relative_value)
    if candidate.is_absolute():
        raise ValueError(f"Project setting must be relative: {relative_value}")
    resolved = (root / candidate).resolve()
    resolved.relative_to(root)
    return resolved


def load_config(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    config = dict(DEFAULT_CONFIG)
    config_path = root / ".garment-flatlay.json"
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"Configuration must be a JSON object: {config_path}")
        config.update(loaded)

    required_strings = ("input_dir", "output_dir", "processed_dir", "output_pattern", "state_file")
    for key in required_strings:
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(f"Configuration value {key!r} must be a non-empty string")
    if not isinstance(config.get("supported_extensions"), list):
        raise ValueError("supported_extensions must be a JSON array")

    config["input_path"] = project_path(root, config["input_dir"])
    config["output_path"] = project_path(root, config["output_dir"])
    config["processed_path"] = project_path(root, config["processed_dir"])
    config["state_path"] = project_path(root, config["state_file"])
    config["extensions"] = {
        str(extension).lower()
        if str(extension).startswith(".")
        else f".{str(extension).lower()}"
        for extension in config["supported_extensions"]
    }
    config["settle_seconds"] = max(0.0, float(config.get("settle_seconds", 3)))
    return config


def initialize(project_root: Path) -> dict[str, Any]:
    config = load_config(project_root)
    config["input_path"].mkdir(parents=True, exist_ok=True)
    config["output_path"].mkdir(parents=True, exist_ok=True)
    config["processed_path"].mkdir(parents=True, exist_ok=True)
    return {
        "status": "ready",
        "input_dir": str(config["input_path"]),
        "output_dir": str(config["output_path"]),
        "processed_dir": str(config["processed_path"]),
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "records": {}}
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    if not isinstance(state, dict) or not isinstance(state.get("records"), dict):
        raise ValueError(f"Invalid queue state: {path}")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def locked_state(config: dict[str, Any]) -> Iterator[dict[str, Any]]:
    state_path: Path = config["state_path"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        state = load_state(state_path)
        yield state
        save_state(state_path, state)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def input_key(project_root: Path, path: Path) -> str:
    stat = path.stat()
    relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    identity = f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def output_for(config: dict[str, Any], input_path: Path) -> Path:
    rendered = config["output_pattern"].format(
        stem=input_path.stem,
        name=input_path.name,
        suffix=input_path.suffix.lower(),
    )
    candidate = Path(rendered)
    if candidate.is_absolute() or len(candidate.parts) != 1:
        raise ValueError("output_pattern must produce one filename, not a path")
    if candidate.suffix.lower() != ".png":
        raise ValueError("output_pattern must produce a .png filename")
    output = (config["output_path"] / candidate).resolve()
    output.relative_to(config["output_path"])
    return output


def ready_inputs(config: dict[str, Any]) -> list[Path]:
    input_dir: Path = config["input_path"]
    if not input_dir.exists():
        return []
    now = time.time()
    candidates: list[tuple[int, str, Path]] = []
    for path in input_dir.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in config["extensions"]:
            continue
        stat = path.stat()
        if now - stat.st_mtime < config["settle_seconds"]:
            continue
        candidates.append((stat.st_mtime_ns, path.name.casefold(), path.resolve()))
    candidates.sort()
    return [item[2] for item in candidates]


def _claim_or_peek(
    project_root: Path,
    *,
    mutate: bool,
    retry_failed: bool = False,
) -> dict[str, Any]:
    root = project_root.resolve()
    initialize(root)
    config = load_config(root)

    def inspect(state: dict[str, Any]) -> dict[str, Any]:
        first_conflict: dict[str, Any] | None = None
        for input_path in ready_inputs(config):
            key = input_key(root, input_path)
            existing = state["records"].get(key)
            if existing and existing.get("status") in {"processing", "complete", "blocked"}:
                continue
            if existing and existing.get("status") == "failed" and not retry_failed:
                continue

            output_path = output_for(config, input_path)
            relative_input = input_path.relative_to(root).as_posix()
            relative_output = output_path.relative_to(root).as_posix()
            if output_path.exists():
                conflict = {
                    "status": "blocked",
                    "key": key,
                    "input": str(input_path),
                    "output": str(output_path),
                    "error": "Refusing to overwrite an existing output",
                }
                if mutate:
                    state["records"][key] = {
                        **conflict,
                        "input": relative_input,
                        "output": relative_output,
                        "updated_at": utc_now(),
                    }
                if first_conflict is None:
                    first_conflict = conflict
                continue

            claim = {
                "status": "claimed" if mutate else "ready",
                "key": key,
                "input": str(input_path),
                "output": str(output_path),
            }
            if mutate:
                state["records"][key] = {
                    "status": "processing",
                    "input": relative_input,
                    "output": relative_output,
                    "updated_at": utc_now(),
                }
            return claim
        return first_conflict or {"status": "empty"}

    if mutate:
        with locked_state(config) as state:
            return inspect(state)
    state = load_state(config["state_path"])
    return inspect(state)


def claim_next(project_root: Path, retry_failed: bool = False) -> dict[str, Any]:
    return _claim_or_peek(project_root, mutate=True, retry_failed=retry_failed)


def peek_next(project_root: Path, retry_failed: bool = False) -> dict[str, Any]:
    return _claim_or_peek(project_root, mutate=False, retry_failed=retry_failed)


def verify_png(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Output does not exist: {path}")
    if path.stat().st_size <= len(PNG_SIGNATURE):
        raise ValueError(f"Output is empty or truncated: {path}")
    with path.open("rb") as handle:
        if handle.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
            raise ValueError(f"Output is not a PNG file: {path}")


def complete_claim(project_root: Path, key: str, output: Path) -> dict[str, Any]:
    root = project_root.resolve()
    config = load_config(root)
    output_path = output.resolve()
    output_path.relative_to(config["output_path"])
    verify_png(output_path)
    with locked_state(config) as state:
        record = state["records"].get(key)
        if not record:
            raise KeyError(f"Unknown claim key: {key}")
        expected = project_path(root, record["output"])
        if expected != output_path:
            raise ValueError(f"Output does not match claim: expected {expected}, got {output_path}")
        input_path = project_path(root, record["input"])
        processed_path = (config["processed_path"] / input_path.name).resolve()
        processed_path.relative_to(config["processed_path"])
        if input_path.exists() and processed_path.exists():
            raise FileExistsError(f"Refusing to overwrite processed input: {processed_path}")
        if input_path.exists():
            os.rename(input_path, processed_path)
        elif not processed_path.is_file():
            raise FileNotFoundError(f"Claimed input is missing: {input_path}")
        if processed_path.stat().st_size <= 0:
            raise ValueError(f"Processed input is empty: {processed_path}")
        record.update(
            status="complete",
            output=output_path.relative_to(root).as_posix(),
            processed_input=processed_path.relative_to(root).as_posix(),
            size=output_path.stat().st_size,
            updated_at=utc_now(),
        )
    return {"status": "complete", "key": key, "output": str(output_path)}


def fail_claim(project_root: Path, key: str, error: str) -> dict[str, Any]:
    root = project_root.resolve()
    config = load_config(root)
    with locked_state(config) as state:
        record = state["records"].get(key)
        if not record:
            raise KeyError(f"Unknown claim key: {key}")
        if record.get("status") != "complete":
            record.update(status="failed", error=error[:1000], updated_at=utc_now())
    return {"status": "failed", "key": key, "error": error}


def emit(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "claim", "peek"):
        child = subparsers.add_parser(command)
        child.add_argument("--project-root", type=Path, required=True)
        if command in {"claim", "peek"}:
            child.add_argument("--retry-failed", action="store_true")
    complete = subparsers.add_parser("complete")
    complete.add_argument("--project-root", type=Path, required=True)
    complete.add_argument("--key", required=True)
    complete.add_argument("--output", type=Path, required=True)
    fail = subparsers.add_parser("fail")
    fail.add_argument("--project-root", type=Path, required=True)
    fail.add_argument("--key", required=True)
    fail.add_argument("--error", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "init":
            result = initialize(args.project_root)
        elif args.command == "claim":
            result = claim_next(args.project_root, args.retry_failed)
        elif args.command == "peek":
            result = peek_next(args.project_root, args.retry_failed)
        elif args.command == "complete":
            result = complete_claim(args.project_root, args.key, args.output)
        else:
            result = fail_claim(args.project_root, args.key, args.error)
        emit(result)
        return 0
    except Exception as exc:  # Present concise machine-readable failures to Codex.
        emit({"status": "error", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
