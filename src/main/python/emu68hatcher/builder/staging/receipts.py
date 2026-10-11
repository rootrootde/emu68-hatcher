"""Record which package wrote which staged file, and turn that into build receipts.

The receipts are what this build observed, not publisher-signed ownership. The
Amiga package tool keeps its own ledger (Emu68-Hatcher/Packages/installed.json),
which only accepts entries backed by signed package sets, so a fresh image stays
"untracked" there until an explicit adopt with a signed set. These records give
that adoption, and anyone diagnosing a card, the facts from the build.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from emu68hatcher.builder.staging.files import resolve_source_path
from emu68hatcher.data.package_identity import (
    CONTENT_ENGINE,
    content_identity,
    effective_native_policy,
)
from emu68hatcher.data.package_schema import Package

RECEIPTS_DIR = "Emu68-Hatcher/Packages"
RECEIPTS_FILE = f"{RECEIPTS_DIR}/build-receipts.json"
RECEIPTS_FORMAT = "hatcher-build-receipts-1"
_MARKER = " - Added by Emu68 Hatcher - "


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class _Write:
    package: str
    sha256: str


@dataclass
class StagingWriteLog:
    """every file a package rule or the user's extras wrote below the boot staging root"""

    root: Path
    writes: dict[str, list[_Write]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)  # key -> staged spelling
    extras: set[str] = field(default_factory=set)

    def _key(self, path: Path) -> str | None:
        try:
            relative = path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return None
        text = relative.as_posix()
        self.names[text.casefold()] = text
        return text.casefold()

    def package_wrote(self, package: str, path: Path) -> None:
        key = self._key(path)
        if key is not None and path.is_file():
            self.writes.setdefault(key, []).append(_Write(package, sha256_file(path)))

    def extra_wrote(self, path: Path) -> None:
        key = self._key(path)
        if key is not None:
            self.extras.add(key)


def find_block(text: bytes, name: str) -> bytes | None:
    """the builder's marker block for name, BEGIN line through END line, if exactly one"""
    begin = f";{name}{_MARKER}BEGIN".encode("iso-8859-1")
    end = f";{name}{_MARKER}END".encode("iso-8859-1")
    lines = text.split(b"\n")
    starts = [i for i, line in enumerate(lines) if line == begin]
    stops = [i for i, line in enumerate(lines) if line == end]
    if len(starts) != 1 or len(stops) != 1 or stops[0] < starts[0]:
        return None
    block = b"\n".join(lines[starts[0] : stops[0] + 1])
    return block + b"\n" if stops[0] + 1 < len(lines) else block


def _preserved(path: str, patterns: list[str]) -> bool:
    lowered = path.casefold()
    return any(fnmatchcase(lowered, pattern.casefold()) for pattern in patterns)


def _file_entries(name: str, log: StagingWriteLog, policy) -> list[dict]:
    entries = []
    for key, writes in sorted(log.writes.items()):
        mine = [w for w in writes if w.package == name]
        if not mine:
            continue
        path = log.names[key]
        staged = log.root / path
        entry: dict = {"path": path, "preserve": _preserved(path, policy.preserve)}
        if not staged.is_file():
            entry["state"] = "removed"
            entries.append(entry)
            continue
        final = sha256_file(staged)
        entry.update(size=staged.stat().st_size, sha256=final)
        last = writes[-1]
        if key in log.extras:
            entry["state"] = "locally-modified"
        elif last.package != name and final != mine[-1].sha256:
            entry.update(state="overridden", by=last.package)
        else:
            sharers = sorted({w.package for w in writes if w.package != name and w.sha256 == final})
            entry["state"] = "shared" if sharers else "owned"
            if sharers:
                entry["shared_with"] = sharers
            # icon layout, stack patches and similar build steps changed the bytes
            # after the rule copied them; this is still the stock file for this image
            entry["build_modified"] = final != mine[-1].sha256
        entries.append(entry)
    return entries


def _block_entries(package: Package, root: Path, user_archives: set[str]) -> list[dict]:
    entries = []
    for script in package.scripts:
        if script.when_user_archive is not None and script.when_user_archive != (
            package.name in user_archives
        ):
            continue
        target = root / script.target
        text = target.read_bytes() if target.is_file() else b""
        block = find_block(text, script.name)
        entries.append(
            {
                "target": script.target,
                "name": script.name,
                "sha256": hashlib.sha256(block).hexdigest() if block else None,
            }
        )
    return entries


def build_receipts(
    *,
    log: StagingWriteLog,
    install_order: list[str],
    packages: dict[str, Package],
    requested: set[str],
    user_archives: set[str],
    local_root: Path | None,
    catalog: dict,
    builder_version: str,
) -> dict:
    """receipts for every package that left something on the image"""
    receipts = []
    skipped = []
    for name in install_order:
        package = packages.get(name)
        if package is None:
            continue
        policy = effective_native_policy(package)
        files = _file_entries(name, log, policy)
        blocks = _block_entries(package, log.root, user_archives)
        menus = [
            {"title": m.title, "path": m.path, "menu": m.menu}
            for m in [*([package.menu_entry] if package.menu_entry else []), *package.menu_entries]
        ]
        relocated = [
            {"from": r.source, "to": r.dest}
            for r in package.relocate
            if resolve_source_path(log.root, f"{r.dest.strip('/')}/{Path(r.source).name}")
        ]
        if package.install and not files:
            skipped.append({"id": name, "reason": "install rules wrote no files"})
            continue
        if not files and not any(b["sha256"] for b in blocks) and not relocated:
            skipped.append(
                {"id": name, "reason": "no files of its own (install media or configuration)"}
            )
            continue
        receipt = {
            "id": name,
            "upstream_version": package.upstream_version,
            "content_identity": content_identity(package, packages, local_root),
            "selection": (
                "requested"
                if name in requested
                else "mandatory"
                if package.mandatory
                else "dependency"
            ),
            "native_eligibility": policy.eligibility.value,
            "reboot": policy.reboot,
            "files": files,
            "blocks": blocks,
            "menu": menus,
            "relocated": relocated,
        }
        if any(s.when_user_archive is not None for s in package.scripts) or (name in user_archives):
            # provenance only: never the archive path, key files or registration data
            receipt["user_archive"] = name in user_archives
        receipts.append(receipt)
    return {
        "format": RECEIPTS_FORMAT,
        "domain": "amiga",
        "authority": "build observation, not signed ownership; adopt with a signed package set",
        "builder": {"name": "Emu68 Hatcher", "version": builder_version},
        "catalog": catalog,
        "identity_engine": CONTENT_ENGINE,
        "packages": receipts,
        "not_recorded": skipped,
    }


def write_json(path: Path, document: dict) -> None:
    """ASCII JSON with LF endings, like every other build-time Amiga text file"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=1, sort_keys=True, ensure_ascii=True)
    path.write_text(text + "\n", encoding="ascii", newline="\n")
