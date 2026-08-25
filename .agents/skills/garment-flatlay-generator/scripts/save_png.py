#!/usr/bin/env python3
"""Copy a generated PNG to its final path without ever overwriting."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def verify_png(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Source does not exist: {path}")
    if path.stat().st_size <= len(PNG_SIGNATURE):
        raise ValueError(f"PNG is empty or truncated: {path}")
    with path.open("rb") as handle:
        if handle.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
            raise ValueError(f"File is not a PNG: {path}")


def save_png(source: Path, destination: Path) -> dict[str, object]:
    source = source.resolve()
    destination = destination.resolve()
    verify_png(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        verify_png(destination)
    except Exception:
        if destination.exists():
            destination.unlink()
        raise
    return {
        "status": "saved",
        "path": str(destination),
        "size": destination.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = save_png(args.source, args.destination)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
