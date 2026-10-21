"""Ed25519 signing envelope for update manifests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from emu68hatcher.data.manifest_models import (
    SignedManifest,
    UpdateManifestV1,
    UpdateManifestV2,
    parse_manifest_payload,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_REFERENCE_DIR = Path(__file__).parent / "reference"
PUBLIC_KEY_PATH = _REFERENCE_DIR / "update_manifest_public_key.pem"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _load_public_key(path: Path = PUBLIC_KEY_PATH) -> Ed25519PublicKey:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("manifest public key is not Ed25519")
    return key


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def verify_signed_payload(content: bytes, public_key_path: Path = PUBLIC_KEY_PATH) -> dict:
    """the payload of a signed envelope, once its signature checks out"""
    from cryptography.exceptions import InvalidSignature

    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    try:
        raw = json.loads(content, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"invalid manifest JSON: {error}") from error
    envelope = SignedManifest.model_validate(raw)
    try:
        signature = base64.b64decode(envelope.signature.value, validate=True)
    except ValueError as error:
        raise ValueError("manifest signature is not valid base64") from error
    try:
        _load_public_key(public_key_path).verify(signature, canonical_payload(envelope.payload))
    except InvalidSignature as error:
        raise ValueError("manifest signature is invalid") from error
    if envelope.signature.key_id != "updates-2026":
        raise ValueError("unknown manifest signing key id")
    return envelope.payload


def verify_manifest_bytes(
    content: bytes,
    public_key_path: Path = PUBLIC_KEY_PATH,
) -> UpdateManifestV1 | UpdateManifestV2:
    return parse_manifest_payload(verify_signed_payload(content, public_key_path))
