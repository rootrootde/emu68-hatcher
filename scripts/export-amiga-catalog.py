#!/usr/bin/env python3
"""Export amiga-catalog-v1.json, the Amiga package tool's view of the package catalog.

The output is the unsigned hatcher-native-metadata-1 audit payload (feed
hatcher-packages) that hatcher-packages' native reader accepts, wrapping a
hatcher-native-catalog-draft-1 catalog. It is generated from the validated YAML
catalog; there is no second package list. Signing is a separate publisher step.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "main" / "python"))

from emu68hatcher.data.catalog import (  # noqa: E402
    DATA_DIR,
    CatalogData,
    CatalogSnapshot,
    load_catalog_source,
    read_catalog_yaml,
)
from emu68hatcher.data.package_identity import (  # noqa: E402
    archive_chain,
    canonical_json,
    effective_native_policy,
)
from emu68hatcher.data.package_schema import NativeEligibility, Package  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

PAYLOAD_FORMAT = "hatcher-native-metadata-1"
FEED = "hatcher-packages"
DATA_SCHEMA = "packages-audit-1"
CATALOG_FORMAT = "hatcher-native-catalog-draft-1"
AUDIT_ENGINE = "audit-only-1"
MAX_BYTES = 524288
MAX_NODES = 32768
MAX_DEPTH = 16
DEFAULT_LOCK = ROOT / "updates" / "amiga-artifacts.lock.yaml"
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


class ArtifactPin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(pattern=r"^https://[^/@\\# ]+/[^\\# ]*$")
    filename: str = Field(pattern=r"^[^/\\\x00-\x1f]+$")
    size: int = Field(gt=0, le=32 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    md5: str = Field(pattern=r"^[0-9a-f]{32}$")
    review: str = Field(min_length=1)


class ArtifactLock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts: dict[str, ArtifactPin] = Field(default_factory=dict)


def load_lock(path: Path = DEFAULT_LOCK) -> dict[str, ArtifactPin]:
    return ArtifactLock.model_validate(read_catalog_yaml(path) or {}).artifacts


def check_lock(data: CatalogData, lock: dict[str, ArtifactPin]) -> None:
    """a pin must still describe the archive the YAML downloads"""
    errors = []
    for name, pin in sorted(lock.items()):
        package = data.packages.get(name)
        download = package.download if package else None
        if download is None or download.source.value == "local":
            errors.append(f"{name}: lock entry without a remote download")
            continue
        if (download.hash or "").lower() != pin.md5:
            errors.append(
                f"{name}: YAML md5 {download.hash} differs from the reviewed lock; "
                "review the new archive and update the lock deliberately"
            )
        if download.filename and download.filename != pin.filename:
            errors.append(f"{name}: filename {download.filename} differs from the lock")
    if errors:
        raise ValueError("artifact lock mismatch:\n  " + "\n  ".join(errors))


def requires_cycles(packages: dict[str, Package]) -> set[str]:
    """packages on a hard-requires cycle through any provider"""
    providers: dict[str, list[str]] = {}
    for package in packages.values():
        for token in {package.name, *package.provides}:
            providers.setdefault(token, []).append(package.name)
    edges = {
        name: {p for token in package.requires for p in providers.get(token, []) if p != name}
        for name, package in packages.items()
    }
    cyclic: set[str] = set()
    for start in packages:
        stack, seen = list(edges[start]), set()
        while stack:
            current = stack.pop()
            if current == start:
                cyclic.add(start)
                break
            if current not in seen:
                seen.add(current)
                stack.extend(edges[current])
    return cyclic


def native_blockers(package: Package, terminal: Package, pin: ArtifactPin | None) -> list[str]:
    """recipe parts the native executor cannot apply"""
    blockers = []
    if pin is None:
        blockers.append("no reviewed artifact pin in updates/amiga-artifacts.lock.yaml")
    for rule in package.install:
        if "**" in rule.source.split("/") or any(c in rule.source for c in "?[]"):
            blockers.append(f"install pattern {rule.source!r} has no native equivalent")
    for script in package.scripts:
        if script.target != "S/User-Startup":
            blockers.append(f"startup block targets {script.target}")
    if len(package.menu_entries) + (package.menu_entry is not None) > 1:
        blockers.append("more than one menu entry")
    if terminal.download is None or terminal.download.source.value == "local":
        blockers.append("archive ships with the desktop app and has no download")
    return blockers


def _reasons(package: Package, pin: ArtifactPin | None, cyclic: bool) -> list[str]:
    policy = effective_native_policy(package)
    head = f"Hatcher policy: {policy.eligibility.value}"
    reasons = [f"{head}: {policy.reason}" if policy.reason else head]
    if pin is not None:
        reasons.append("Reviewed archive size and SHA-256 are in the source descriptor.")
    reasons.append("Audit entry only: installing needs a signed package plan.")
    if policy.preserve:
        reasons.append("Keeps changed files: " + ", ".join(policy.preserve))
    if policy.reboot == "cold":
        reasons.append("Changes need a cold restart.")
    if cyclic:
        reasons.append("Hard dependency cycle; the native resolver cannot order it.")
    return reasons


def _source_descriptor(terminal: Package, pin: ArtifactPin | None) -> str:
    if terminal.download is None:
        return "null"
    source = terminal.download.model_dump(mode="json")
    source["legacy_md5"] = source.pop("hash")
    if pin is not None:
        source.update(pinned_url=pin.url, sha256=pin.sha256, size=pin.size)
    return canonical_json(source).decode("utf-8")


def package_entry(
    package: Package,
    packages: dict[str, Package],
    lock: dict[str, ArtifactPin],
    cyclic: set[str],
) -> dict:
    chain = archive_chain(package, packages)
    terminal = chain[-1]
    pin = lock.get(terminal.name)
    policy = effective_native_policy(package)
    if policy.eligibility == NativeEligibility.SUPPORTED:
        blockers = native_blockers(package, terminal, pin)
        if package.name in cyclic:
            blockers.append("hard dependency cycle")
        if blockers:
            raise ValueError(f"{package.name} is marked supported but: " + "; ".join(blockers))
    recipe = hashlib.sha256(
        canonical_json(
            {
                "engine": AUDIT_ENGINE,
                "chain": [p.model_dump(mode="json", by_alias=True) for p in chain],
            }
        )
    ).hexdigest()
    return {
        "id": package.name,
        "title": package.friendly_name,
        "description": package.description,
        "category": package.group,
        "bundle": package.bundle or "",
        "kickstart": package.versions,
        "emu68": package.emu68_versions or [],
        # the draft reader accepts only null here; the label lives in the desktop catalog
        "upstream_version": None,
        "purchase_url": package.purchase_url or "",
        "source": _source_descriptor(terminal, pin),
        "archive_package": package.archive_package or "",
        "requires": package.requires,
        "recommends": package.recommends,
        "provides": package.provides,
        "conflicts": package.conflicts,
        "mandatory": package.mandatory,
        "default": package.default,
        "eligibility": "unreviewed",
        "reasons": _reasons(package, pin, package.name in cyclic),
        "recipe_identity": recipe,
        "content_identity": None,
    }


def export_catalog(snapshot: CatalogSnapshot, lock: dict[str, ArtifactPin], revision: int) -> dict:
    data = snapshot.data()
    check_lock(data, lock)
    cyclic = requires_cycles(data.packages)
    catalog = {
        "format": CATALOG_FORMAT,
        "trust": "UNTRUSTED",
        "engine": AUDIT_ENGINE,
        "revision": snapshot.revision,
        "source_commit": snapshot.source_commit,
        "packages": [
            package_entry(package, data.packages, lock, cyclic)
            for _, package in sorted(data.packages.items())
        ],
        "bundles": [b.model_dump(mode="json") for _, b in sorted(data.bundles.items())],
    }
    payload = {
        "format": PAYLOAD_FORMAT,
        "feed": FEED,
        "schema_version": 1,
        "min_native_client": 1,
        "revision": revision,
        "source_commit": snapshot.source_commit,
        "data_schema": DATA_SCHEMA,
        "data": {"policy": "audit-only", "catalog_audit": catalog},
    }
    validate(payload)
    return payload


def _text(value: str, maximum: int, identifier: bool = False) -> None:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or (identifier and not _IDENTIFIER.fullmatch(value))
    ):
        raise ValueError(f"value exceeds native catalog limits: {value[:60]!r}")


def _nodes(value, depth: int = 0) -> int:
    if depth > MAX_DEPTH:
        raise ValueError("native depth limit")
    if isinstance(value, dict):
        return 1 + sum(1 + _nodes(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(_nodes(v, depth + 1) for v in value)
    if value is None or type(value) in (str, bool):
        return 1
    if type(value) is int and -2147483648 <= value <= 2147483647:
        return 1
    raise ValueError("only bounded integer JSON numbers are allowed")


def validate(payload: dict) -> None:
    """the bounds hatcher-packages' native reader enforces, checked before publishing"""
    catalog = payload["data"]["catalog_audit"]
    if not 1 <= payload["revision"] <= 2147483647:
        raise ValueError("revision must be 1..2147483647")
    if not re.fullmatch("[0-9a-f]{40}", payload["source_commit"]):
        raise ValueError("source commit must be 40 lowercase hex digits")
    if not 1 <= len(catalog["packages"]) <= 256 or len(catalog["bundles"]) > 64:
        raise ValueError("native package/bundle limit exceeded")
    for p in catalog["packages"]:
        for field, maximum in {
            "id": 63,
            "title": 256,
            "description": 4096,
            "category": 128,
            "bundle": 63,
            "purchase_url": 2048,
            "source": 4096,
            "archive_package": 63,
        }.items():
            _text(
                p[field],
                maximum,
                field == "id" or (field in {"bundle", "archive_package"} and bool(p[field])),
            )
        for field in ("kickstart", "emu68", "requires", "recommends", "provides", "conflicts"):
            values = p[field]
            if len(values) > 32 or len(values) != len(set(values)):
                raise ValueError(f"{p['id']}: {field} list limit or duplicate")
            for value in values:
                if not value:
                    raise ValueError(f"{p['id']}: empty {field} entry")
                _text(value, 63, field not in {"kickstart", "emu68"})
        reasons = p["reasons"]
        if not reasons or len(reasons) > 32 or len(reasons) != len(set(reasons)):
            raise ValueError(f"{p['id']}: reasons must be 1..32 unique strings")
        for reason in reasons:
            if not reason:
                raise ValueError(f"{p['id']}: empty reason")
            _text(reason, 512)
    for b in catalog["bundles"]:
        for field, maximum in {
            "id": 63,
            "display_name": 256,
            "group": 128,
            "description": 4096,
        }.items():
            _text(b[field], maximum, field == "id")
    if _nodes(payload) > MAX_NODES:
        raise ValueError("native node limit exceeded")
    # reserve room for the signature envelope the publisher adds
    signature = {"algorithm": "ed25519", "key_id": "updates-2026", "value": "A" * 88}
    envelope = {"payload": payload, "signature": signature}
    if len(canonical_json(envelope)) + 1 > MAX_BYTES:
        raise ValueError("signed catalog would exceed the 512 KiB native input limit")


def _source_commit(allow_dirty: bool) -> str:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()

    inputs = ["src/main/python/emu68hatcher/data", "updates/amiga-artifacts.lock.yaml"]
    dirty = git("status", "--porcelain", "--untracked-files=all", "--", *inputs)
    if dirty and not allow_dirty:
        raise ValueError(
            "catalog inputs differ from HEAD, so the source commit label would be wrong:\n" + dirty
        )
    return git("rev-parse", "HEAD")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--packages-dir", type=Path, default=DATA_DIR / "packages")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="local preview only: label uncommitted inputs with HEAD",
    )
    args = parser.parse_args()
    if not 1 <= args.revision <= 2147483647:
        parser.error("revision must be 1..2147483647")
    if args.output.exists():
        parser.error(f"{args.output} exists; published catalogs are never overwritten")
    commit = _source_commit(args.allow_dirty)
    snapshot = CatalogSnapshot.create(
        load_catalog_source(args.packages_dir), revision=args.revision, source_commit=commit
    )
    payload = export_catalog(snapshot, load_lock(args.lock), args.revision)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as file:
        file.write(content + "\n")
    supported = [
        p["id"]
        for p in payload["data"]["catalog_audit"]["packages"]
        if p["reasons"][0].startswith("Hatcher policy: supported")
    ]
    print(
        f"wrote {args.output}: {len(payload['data']['catalog_audit']['packages'])} packages, "
        f"supported: {', '.join(supported) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
