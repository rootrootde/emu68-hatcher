#!/usr/bin/env python3
"""Convert a Picasso96Settings file to JSON (inspect) or JSON back to the binary (generate)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "main" / "python"))

from emu68hatcher.data.picasso96 import decode_settings, encode_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "generate"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "inspect":
            text = json.dumps(
                decode_settings(args.source.read_bytes()), indent=2, ensure_ascii=True
            )
            args.destination.write_text(text + "\n", encoding="utf-8")
        else:
            settings = json.loads(args.source.read_text(encoding="utf-8"))
            args.destination.write_bytes(encode_settings(settings))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
