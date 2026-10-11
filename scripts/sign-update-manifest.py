#!/usr/bin/env python3
"""Sign an update manifest payload."""

import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from emu68hatcher.data.update_manifest import (
    _MAX_MANIFEST_BYTES,
    canonical_payload,
    parse_manifest_payload,
)
from pydantic import ValidationError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--key-id", default="updates-2026")
    args = parser.parse_args()

    try:
        payload = json.loads(args.source.read_text(encoding="utf-8"))
        parse_manifest_payload(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        parser.error(str(error))

    key = load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        parser.error("private key is not Ed25519")

    signature = base64.b64encode(key.sign(canonical_payload(payload))).decode("ascii")
    envelope = {
        "payload": payload,
        "signature": {
            "algorithm": "ed25519",
            "key_id": args.key_id,
            "value": signature,
        },
    }
    content = json.dumps(envelope, indent=2, ensure_ascii=False) + "\n"
    if args.key_id != "updates-2026":
        parser.error("unknown signing key id")
    if len(content.encode("utf-8")) > _MAX_MANIFEST_BYTES:
        parser.error("signed manifest exceeds client size limit")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
