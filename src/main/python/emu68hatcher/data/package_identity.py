"""Derived package identities and live-install policy (package metadata P1)."""

from __future__ import annotations

import hashlib
import json
from fnmatch import fnmatchcase
from pathlib import Path

from emu68hatcher.data.package_schema import NativeEligibility, NativePolicy, Package

# label carried next to every content identity. It is not a native plan payload hash
# and not the audit recipe hash; never compare it with either.
CONTENT_ENGINE = "hatcher-content-1"

_PROTECTED_GROUPS = {"System", "Drivers", "RTG", "Network", "Locale"}
# fields that decide which bytes land where; display text and dependency edges are left
# out so a description fix does not look like new content
_RECIPE_FIELDS = (
    "name",
    "archive_package",
    "install",
    "relocate",
    "scripts",
    "menu_entry",
    "menu_entries",
)


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def effective_native_policy(package: Package) -> NativePolicy:
    """the package's declared policy, or the conservative one its recipe implies"""
    if package.native is not None:
        return package.native
    if package.mandatory or package.group in _PROTECTED_GROUPS:
        return NativePolicy(
            eligibility=NativeEligibility.PROTECTED,
            reason="System, driver or network content; image builds and Emu68 Manager own it",
        )
    if package.purchase_url or any(s.when_user_archive is not None for s in package.scripts):
        return NativePolicy(
            eligibility=NativeEligibility.USER_ARCHIVE,
            reason="Needs an archive the user supplies",
        )
    return NativePolicy(
        eligibility=NativeEligibility.UNSUPPORTED,
        reason="Recipe not reviewed for live installation",
    )


def archive_chain(package: Package, packages: dict[str, Package]) -> list[Package]:
    """the package followed by every shared-archive ancestor, ending at the download"""
    chain = [package]
    seen = {package.name}
    while chain[-1].archive_package:
        parent = packages.get(chain[-1].archive_package)
        if parent is None or parent.name in seen:
            raise ValueError(f"{package.name}: broken archive chain")
        seen.add(parent.name)
        chain.append(parent)
    return chain


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_matches(root: Path, pattern: str) -> list[Path]:
    matches = [root]
    for part in pattern.split("/"):
        matches = [
            child
            for parent in matches
            if parent.is_dir()
            for child in sorted(parent.iterdir())
            if fnmatchcase(child.name.lower(), part.lower())
        ]
    return matches


def local_inputs(package: Package, terminal: Package, local_root: Path) -> dict[str, str]:
    """sha256 of every host file a local package installs from, keyed by relative path"""
    download = terminal.download
    if download is None or download.source.value != "local":
        return {}
    if download.path:
        archive = local_root / download.path
        return {download.path: _sha256_file(archive) if archive.is_file() else "missing"}
    result: dict[str, str] = {}
    for rule in package.install:
        found = _local_matches(local_root, rule.source)
        if not found:
            result[rule.source] = "missing"
        for match in found:
            files = (
                sorted(p for p in match.rglob("*") if p.is_file()) if match.is_dir() else [match]
            )
            for file in files:
                result[file.relative_to(local_root).as_posix()] = _sha256_file(file)
    return result


def content_identity(
    package: Package, packages: dict[str, Package], local_root: Path | None = None
) -> str:
    """sha256 that changes when installed files, rules or the shared archive change"""
    chain = archive_chain(package, packages)
    terminal = chain[-1]
    recipe = package.model_dump(mode="json", by_alias=True, include=set(_RECIPE_FIELDS))
    policy = effective_native_policy(package)
    document = {
        "engine": CONTENT_ENGINE,
        "recipe": recipe,
        "preserve": policy.preserve,
        "archives": [
            {
                "name": source.name,
                "download": source.download.model_dump(mode="json") if source.download else None,
            }
            for source in chain[1:]
        ],
        "local": local_inputs(package, terminal, local_root) if local_root else {},
    }
    if terminal is package:
        document["download"] = (
            package.download.model_dump(mode="json") if package.download else None
        )
    return hashlib.sha256(canonical_json(document)).hexdigest()
