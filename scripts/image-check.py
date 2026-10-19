#!/usr/bin/env python3
"""build an image without the gui, list and check its files, compare two builds"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "main" / "python"))

MANIFEST_FORMAT = "hatcher-image-manifest-1"
FAT_NAME = "EMU68BOOT"
RECEIPTS = "emu68-hatcher/packages/build-receipts.json"
INSTALLATION = "emu68-hatcher/installation.json"
# text kept in the manifest so compare can show a diff
TEXT_DIRS = ("s/",)
TEXT_LIMIT = 256 * 1024
UAEFSDB = "_UAEFSDB.___"
MARKER_RE = re.compile(rb"^;(.+) - Added by Emu68 Hatcher - (BEGIN|END)$")
HUNK_MAGIC = b"\x00\x00\x03\xf3"

SHELL_BUILTINS = {
    "alias", "ask", "cd", "cli", "echo", "else", "endcli", "endif", "endshell", "endskip",
    "failat", "fault", "get", "getenv", "if", "lab", "newcli", "newshell", "path", "prompt",
    "quit", "resident", "run", "set", "setenv", "skip", "stack", "unalias", "unset",
    "unsetenv", "why",
}  # fmt: skip
# volumes that exist only at runtime or are not files
SKIP_VOLUMES = {"ram", "t", "nil", "con", "raw", "pipe", "clips", "ser", "par", "prt", "ram disk"}
STANDARD_ASSIGNS = {
    "sys": "",
    "c": "C",
    "s": "S",
    "l": "L",
    "libs": "Libs",
    "devs": "Devs",
    "fonts": "Fonts",
    "locale": "Locale",
    "help": "Locale/Help",
    "keymaps": "Devs/Keymaps",
    "printers": "Devs/Printers",
    "rexx": "S",
    "envarc": "Prefs/Env-Archive",
    # ENV: is a RAM copy of ENVARC: made at boot
    "env": "Prefs/Env-Archive",
}  # fmt: skip
# the MUI user-startup block tests a file that never exists on purpose
IGNORE_REFS = {"help:dummy"}
NO_ARG_CHECK = {"delete", "makedir", "rename", "makelink", "copy", "echo", "setenv", "assign"}


def _fold(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


# --- hst-imager ---------------------------------------------------------------------------


def _hst() -> tuple[str, dict[str, str]]:
    from emu68hatcher.utils.host_tools import find_hst_imager, get_hst_imager_env

    exe = find_hst_imager()
    if exe is None:
        sys.exit("hst-imager not found; start the app once or pass --fetch-tools")
    return str(exe), get_hst_imager_env()


def _hst_run(*args: str, timeout: float = 3600) -> subprocess.CompletedProcess:
    exe, env = _hst()
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )


def _hst_json(*args: str) -> dict:
    result = _hst_run(*args, "--format", "Json")
    start = result.stdout.find("{")
    if result.returncode != 0 or start < 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-3:]
        raise RuntimeError(f"hst-imager {' '.join(args)} failed: {' | '.join(tail)}")
    return json.loads(result.stdout[start:])


def _target(image: str):
    return image if image.startswith("\\\\.\\") else Path(image)


def _partitions(image: str) -> tuple[list[dict], list[str]]:
    from emu68hatcher.builder.host.hst_commands import hst_path

    target = _target(image)
    parts: list[dict] = []
    problems: list[str] = []
    for entry in _hst_json("fs", "dir", hst_path(target, "mbr"))["entries"]:
        number = entry["name"]
        fs = entry.get("properties", {}).get("File System", "")
        if fs.startswith("FAT"):
            parts.append({"name": FAT_NAME, "root": hst_path(target, "mbr", number), "fs": fs})
            continue
        if "RDB" not in fs:
            continue
        for device in _hst_json("fs", "dir", hst_path(target, "mbr", number, "rdb"))["entries"]:
            props = device.get("properties", {})
            name = props.get("Device Name")
            if not name:
                problems.append(f"mbr partition {number}, rdb entry {device['name']} is unreadable")
                continue
            parts.append(
                {
                    "name": name,
                    "root": hst_path(target, "mbr", number, "rdb", name),
                    "fs": props.get("Dos Type", ""),
                    "volume": props.get("Volume Name", ""),
                }
            )
    return parts, problems


def _read_uaefsdb(path: Path) -> dict[str, tuple[str, str]]:
    """host name -> (amiga name, comment) from one _UAEFSDB.___ file"""
    names: dict[str, tuple[str, str]] = {}
    data = path.read_bytes()
    for offset in range(0, len(data) - 599, 600):
        record = data[offset : offset + 600]
        if not record[0]:
            continue
        amiga = record[5:262].split(b"\0", 1)[0].decode("iso-8859-1")
        host = record[262:519].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        comment = record[519:600].split(b"\0", 1)[0].decode("iso-8859-1")
        names[host] = (amiga, comment)
    return names


def _extract(part: dict, dest: Path) -> dict[str, Path]:
    """copy a partition out; folded amiga path -> host file"""
    dest.mkdir(parents=True, exist_ok=True)
    args = ["fs", "copy", f"{part['root']}/*", str(dest), "--recursive", "TRUE"]
    args += ["--makedir", "TRUE", "--force", "TRUE"]
    if part["name"] != FAT_NAME:
        args += ["--uaemetadata", "UaeFsDb"]
    result = _hst_run(*args)
    if result.returncode != 0:
        tail = (result.stdout + result.stderr).strip().splitlines()[-3:]
        raise RuntimeError(f"copying {part['name']} out failed: {' | '.join(tail)}")
    files: dict[str, Path] = {}
    for directory, _dirs, names in os.walk(dest):
        here = Path(directory)
        fsdb = here / UAEFSDB
        mapping = _read_uaefsdb(fsdb) if fsdb.is_file() else {}
        rel_dir = here.relative_to(dest)
        amiga_dir = _amiga_rel_dir(dest, rel_dir)
        for name in names:
            if name == UAEFSDB:
                continue
            amiga_name = mapping.get(name, (name, ""))[0]
            rel = f"{amiga_dir}/{amiga_name}" if amiga_dir else amiga_name
            files[_fold(rel)] = here / name
    return files


def _amiga_rel_dir(dest: Path, rel_dir: Path) -> str:
    parts: list[str] = []
    current = dest
    for component in rel_dir.parts:
        fsdb = current / UAEFSDB
        mapping = _read_uaefsdb(fsdb) if fsdb.is_file() else {}
        parts.append(mapping.get(component, (component, ""))[0])
        current = current / component
    return "/".join(parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- manifest -----------------------------------------------------------------------------


def dump(image: str, work: Path, skip: set[str]) -> tuple[dict, dict[str, Path]]:
    """manifest of every partition plus a folded path -> extracted file map"""
    parts, problems = _partitions(image)
    entries: list[dict] = []
    texts: dict[str, str] = {}
    documents: dict[str, object] = {}
    host_files: dict[str, Path] = {}
    for part in parts:
        if part["name"].casefold() in skip:
            problems.append(f"{part['name']} skipped on request")
            continue
        print(f"reading {part['name']} ...", flush=True)
        listing = _hst_json("fs", "dir", part["root"], "-r", "--attributes", "Amiga")
        extracted = _extract(part, work / part["name"])
        for item in listing["entries"]:
            rel = "/".join(item.get("fullPathComponents") or [item["name"]])
            path = f"{part['name']}/{rel}"
            props = item.get("properties") or {}
            entry = {
                "path": path,
                "type": "file" if item.get("type") == 1 else "dir",
                "attrs": item.get("attributes", ""),
            }
            if props.get("Comment"):
                entry["comment"] = props["Comment"]
            if entry["type"] == "file":
                entry["size"] = item.get("size", 0)
                host = extracted.get(_fold(rel))
                if host is None:
                    problems.append(f"{path} is listed but could not be read back")
                else:
                    entry["sha256"] = _sha256(host)
                    host_files[_fold(path)] = host
                    _keep_text(path, rel, host, texts, documents)
            entries.append(entry)
    entries.sort(key=lambda e: _fold(e["path"]))
    manifest = {
        "format": MANIFEST_FORMAT,
        "image": image,
        "host": f"{platform.system()} {platform.machine()}",
        "partitions": [{k: v for k, v in p.items() if k != "root"} for p in parts],
        "problems": problems,
        "entries": entries,
        "texts": texts,
        "documents": documents,
    }
    return manifest, host_files


def _keep_text(path: str, rel: str, host: Path, texts: dict, documents: dict) -> None:
    folded = _fold(rel)
    if folded in (RECEIPTS, INSTALLATION):
        try:
            documents[path] = json.loads(host.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return
    is_config = path.startswith(f"{FAT_NAME}/") and folded == "config.txt"
    if not is_config and not (folded.startswith(TEXT_DIRS) and not folded.endswith(".info")):
        return
    if host.stat().st_size > TEXT_LIMIT:
        return
    data = host.read_bytes()
    if b"\0" in data:
        return
    texts[path] = data.decode("utf-8" if is_config else "iso-8859-1", errors="replace")


# --- checks -------------------------------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, check: str, message: str) -> None:
        self.rows.append((level, check, message))

    def count(self, level: str) -> int:
        return sum(1 for row in self.rows if row[0] == level)

    def text(self, notes: bool = True) -> str:
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        lines = [
            f"{level:5} {check:10} {message}"
            for level, check, message in sorted(self.rows, key=lambda r: order[r[0]])
            if notes or level != "INFO"
        ]
        lines.append(
            f"{self.count('ERROR')} errors, {self.count('WARN')} warnings, "
            f"{self.count('INFO')} notes"
        )
        return "\n".join(lines)


class Volumes:
    """amiga volume, device and assign names -> folded manifest path prefixes"""

    def __init__(self, manifest: dict, boot: str) -> None:
        self.boot = boot
        self.names: dict[str, str | None] = {}
        for part in manifest["partitions"]:
            self.names[_fold(part["name"])] = part["name"]
            if part.get("volume"):
                self.names[_fold(part["volume"])] = part["name"]
        for name, sub in STANDARD_ASSIGNS.items():
            self.names[name] = f"{boot}/{sub}" if sub else boot

    def resolve(self, ref: str) -> str | None:
        """manifest path for an amiga path; None when the volume is unknown"""
        volume, _, rest = ref.partition(":")
        folded = _fold(volume)
        if folded in SKIP_VOLUMES or folded not in self.names:
            return None
        base = self.names[folded]
        if base is None:
            return None
        rest = rest.strip("/")
        return f"{base}/{rest}" if rest else base

    def assign(self, name: str, target: str | None) -> None:
        self.names[_fold(name)] = target


def _tokens(line: str) -> list[str]:
    tokens = []
    for match in re.finditer(r'"[^"]*"|\S+', line):
        token = match.group(0)
        if token.startswith(";"):
            break
        tokens.append(token.strip('"'))
    return tokens


def _is_ref(token: str) -> bool:
    if ":" not in token or token.startswith(("<", ">", "-", "$", "`")):
        return False
    return "$" not in token and "#?" not in token and "*" not in token


def _exists(index: dict[str, dict], path: str) -> bool:
    folded = _fold(path.rstrip("/"))
    return folded in index or "/" not in folded


def check_scripts(manifest: dict, index: dict[str, dict], boot: str, report: Report) -> None:
    volumes = Volumes(manifest, boot)
    search = [f"{boot}/C"]
    order = ["Startup-Sequence", "Hatcher-FirstBoot", "User-Startup", "Shell-Startup"]
    scripts = [f"{boot}/S/{name}" for name in order]
    firstboot = sorted(
        e["path"]
        for e in manifest["entries"]
        if e["type"] == "file"
        and _fold(e["path"]).startswith(_fold(f"{boot}/S/FirstBoot/"))
        and not e["path"].lower().endswith(".info")
    )
    scripts[2:2] = firstboot
    texts = {_fold(k): v for k, v in manifest["texts"].items()}
    receipts = _document(manifest, f"{boot}/{RECEIPTS}") or {}
    package_blocks = {
        block["name"]
        for package in receipts.get("packages", [])
        for block in package.get("blocks", [])
    }
    for script in scripts:
        text = texts.get(_fold(script))
        if text is None:
            if script.endswith(("Startup-Sequence", "User-Startup")):
                report.add("ERROR", "scripts", f"{script} is missing")
            continue
        if text.lstrip().startswith("/*"):
            continue  # arexx
        _check_script(script, text, volumes, search, index, package_blocks, report)


def _check_script(
    script: str,
    text: str,
    volumes: Volumes,
    search: list[str],
    index: dict[str, dict],
    package_blocks: set[str],
    report: Report,
) -> None:
    name = script.split("/", 1)[1]
    guards: list[str] = []
    block = None
    for number, raw in enumerate(text.splitlines(), start=1):
        marker = MARKER_RE.match(raw.strip().encode("iso-8859-1", errors="replace"))
        if marker:
            block = marker.group(1).decode("iso-8859-1") if marker.group(2) == b"BEGIN" else None
            continue
        tokens = _tokens(raw.strip())
        if not tokens or tokens[0].startswith("."):
            continue
        # a missing file inside a package block is a package that lost its own file
        level = "WARN" if block in package_blocks else "INFO"
        where = f"{name}:{number}" + (f" [{block}]" if block else "")
        word = tokens[0].lower()
        if word == "endif":
            if guards:
                guards.pop()
            continue
        if word == "if":
            args = [t.lower() for t in tokens[1:]]
            guard = ""
            if "exists" in args and "not" not in args:
                position = args.index("exists") + 2
                target = tokens[position] if len(tokens) > position else ""
                resolved = volumes.resolve(target) if _is_ref(target) else None
                if resolved is not None and _fold(target) not in IGNORE_REFS:
                    guard = _fold(resolved)
                    if not _exists(index, resolved):
                        report.add(
                            level, "scripts", f"{where} 'If Exists {target}' is false, skipped"
                        )
            guards.append(guard)
            continue
        if word in ("alias", "lab", "skip", "else", "echo", "failat", "quit", "set", "unset"):
            continue
        if word == "path":
            for token in tokens[1:]:
                resolved = volumes.resolve(token) if _is_ref(token) else None
                if resolved:
                    search.append(resolved)
            continue
        tokens = [t for t in tokens if not t.startswith((">", "<"))]
        while tokens and tokens[0].lower() in ("run", "runback"):
            tokens = tokens[1:]
        if not tokens:
            continue
        command = tokens[0]
        base = command.rsplit(":", 1)[-1].rsplit("/", 1)[-1].lower()
        if base == "assign":
            _note_assign(tokens[1:], volumes)
        _check_command(command, where, guards, volumes, search, index, report)
        if base in NO_ARG_CHECK:
            continue
        for token in tokens[1:]:
            value = token.split("=", 1)[1] if "=" in token else token
            if not _is_ref(value):
                continue
            resolved = volumes.resolve(value)
            if resolved is None or _exists(index, resolved) or _fold(resolved) in guards:
                continue
            report.add(level, "scripts", f"{where} {command} names {value}, not on the image")


_ASSIGN_OPTIONS = {"add", "path", "defer", "exists", "remove", "quiet", "late", "dismount"}


def _note_assign(args: list[str], volumes: Volumes) -> None:
    args = [a for a in args if a.lower() not in _ASSIGN_OPTIONS]
    if len(args) >= 2 and args[0].endswith(":") and args[0].count(":") == 1:
        target = volumes.resolve(args[1]) if _is_ref(args[1]) else None
        volumes.assign(args[0][:-1], target)


def _check_command(
    command: str,
    where: str,
    guards: list[str],
    volumes: Volumes,
    search: list[str],
    index: dict[str, dict],
    report: Report,
) -> None:
    if "$" in command or command.startswith(("`", "*")):
        return
    base = command.rsplit(":", 1)[-1].lower()
    if command.lower().startswith("c:") and base in SHELL_BUILTINS:
        return
    if ":" in command:
        resolved = volumes.resolve(command)
        if resolved is None or _exists(index, resolved):
            return
        if _fold(resolved) in guards:
            return
        report.add("ERROR", "scripts", f"{where} runs {command}, which is not on the image")
        return
    if "/" in command or command.lower() in SHELL_BUILTINS:
        return
    for directory in search:
        if _exists(index, f"{directory}/{command}"):
            return
    report.add("INFO", "scripts", f"{where} {command} is not in C: or the path (rom resident?)")


def check_blocks(manifest: dict, boot: str, report: Report) -> None:
    from emu68hatcher.builder.staging.receipts import find_block
    from emu68hatcher.data.catalog import bundled_catalog

    receipts = _document(manifest, f"{boot}/{RECEIPTS}")
    if receipts is None:
        report.add("ERROR", "blocks", "build-receipts.json is missing or unreadable")
        return
    catalog = bundled_catalog()
    texts = {_fold(k): v for k, v in manifest["texts"].items()}
    positions: dict[tuple[str, str], int] = {}
    for package in receipts.get("packages", []):
        definition = catalog.package(package["id"])
        if definition is None:
            continue
        recorded = {b["name"]: b for b in package.get("blocks", [])}
        for script in definition.scripts:
            wanted = script.when_user_archive
            if wanted is not None and wanted != package.get("user_archive", False):
                continue
            text = texts.get(_fold(f"{boot}/{script.target}"))
            data = text.encode("iso-8859-1", errors="replace") if text is not None else b""
            found = find_block(data, script.name)
            receipt = recorded.get(script.name)
            label = f"{package['id']}: block '{script.name}'"
            if found is None:
                if receipt and receipt.get("sha256"):
                    report.add(
                        "ERROR", "blocks", f"{label} is in the receipts but not on the image"
                    )
                continue
            if receipt is None or not receipt.get("sha256"):
                report.add("ERROR", "blocks", f"{label} is on the image, the receipts have no hash")
            elif hashlib.sha256(found).hexdigest() != receipt["sha256"]:
                report.add("ERROR", "blocks", f"{label} changed after the receipts were written")
            begin = f";{script.name} - Added by Emu68 Hatcher - BEGIN".encode("iso-8859-1")
            positions[(package["id"], _fold(script.target))] = data.index(begin)
    present = {p["id"] for p in receipts.get("packages", [])}
    for (name, target), position in positions.items():
        package = catalog.package(name)
        if package is None:
            continue
        for token in package.requires:
            for other in present:
                provider = catalog.package(other)
                if provider is None or not (other == token or token in provider.provides):
                    continue
                other_position = positions.get((other, target))
                if other_position is not None and other_position > position:
                    report.add(
                        "ERROR",
                        "blocks",
                        f"{target}: {name} runs before {other}, which it requires",
                    )


def _document(manifest: dict, path: str):
    for key, value in manifest["documents"].items():
        if _fold(key) == _fold(path):
            return value
    return None


def check_receipts(manifest: dict, index: dict[str, dict], boot: str, report: Report) -> None:
    receipts = _document(manifest, f"{boot}/{RECEIPTS}")
    if receipts is None:
        return
    checked = 0
    for package in receipts.get("packages", []):
        for item in package.get("files", []):
            path = f"{boot}/{item['path']}"
            entry = index.get(_fold(path))
            if item.get("state") == "removed":
                if entry is not None:
                    report.add("ERROR", "receipts", f"{path} ({package['id']}) should be removed")
                continue
            checked += 1
            if entry is None:
                report.add("ERROR", "receipts", f"{path} ({package['id']}) is missing")
            elif entry.get("sha256") and entry["sha256"] != item.get("sha256"):
                report.add(
                    "ERROR", "receipts", f"{path} ({package['id']}) differs from the receipt"
                )
    report.add("INFO", "receipts", f"{checked} receipt files compared")
    for item in receipts.get("not_recorded", []):
        if item.get("reason") == "install rules wrote no files":
            report.add("WARN", "receipts", f"{item['id']}: install rules wrote no files")


def check_text_files(
    manifest: dict, host_files: dict[str, Path], boot: str, report: Report
) -> None:
    for entry in manifest["entries"]:
        if entry["type"] != "file" or entry["path"].startswith(f"{FAT_NAME}/"):
            continue
        folded = _fold(entry["path"])
        rel = folded[len(_fold(f"{boot}/S/")) :] if folded.startswith(_fold(f"{boot}/S/")) else None
        in_s = (
            rel is not None
            and not rel.endswith(".info")
            and rel.count("/") <= (1 if rel.startswith("firstboot/") else 0)
        )
        if not in_s and "S" not in entry.get("attrs", "")[:4]:
            continue
        host = host_files.get(folded)
        if host is None or host.stat().st_size > TEXT_LIMIT:
            continue
        data = host.read_bytes()
        if b"\0" in data:
            continue
        path = entry["path"]
        if data.startswith(b"\xef\xbb\xbf"):
            report.add("ERROR", "encoding", f"{path} starts with a UTF-8 byte order mark")
        if b"\r" in data:
            report.add("ERROR", "encoding", f"{path} has CR line endings")
        if not folded.endswith("wireless.prefs") and any(b > 0x7F for b in data):
            try:
                data.decode("utf-8")
                report.add("WARN", "encoding", f"{path} looks UTF-8, Amiga scripts are Latin-1")
            except UnicodeDecodeError:
                pass
        _check_markers(path, data, report)


def _check_markers(path: str, data: bytes, report: Report) -> None:
    seen: dict[bytes, list[bytes]] = {}
    for line in data.split(b"\n"):
        match = MARKER_RE.match(line)
        if match:
            seen.setdefault(match.group(1), []).append(match.group(2))
    for name, kinds in seen.items():
        if kinds != [b"BEGIN", b"END"]:
            label = name.decode("iso-8859-1")
            report.add("ERROR", "markers", f"{path}: block '{label}' markers {kinds}")


def check_files(manifest: dict, host_files: dict[str, Path], boot: str, report: Report) -> None:
    for entry in manifest["entries"]:
        if entry["type"] != "file":
            continue
        folded = _fold(entry["path"])
        head = folded.split("/", 2)[1] if folded.count("/") >= 2 else ""
        if not folded.startswith(_fold(boot) + "/") or head not in ("c", "libs", "devs"):
            continue
        if folded.endswith(".info"):
            continue
        if entry.get("size") == 0:
            report.add("INFO", "files", f"{entry['path']} is empty")
            continue
        if head != "c":
            continue
        host = host_files.get(folded)
        if host is None:
            continue
        with open(host, "rb") as handle:
            magic = handle.read(4)
        attrs = entry.get("attrs", "")
        if magic == HUNK_MAGIC and len(attrs) == 8 and attrs[6] != "E":
            report.add("WARN", "files", f"{entry['path']} is a program without the E bit")


def check_boot_partition(manifest: dict, index: dict[str, dict], report: Report) -> None:
    text = None
    for key, value in manifest["texts"].items():
        if _fold(key) == _fold(f"{FAT_NAME}/config.txt"):
            text = value
    if text is None:
        report.add("ERROR", "config.txt", "config.txt is missing on the boot partition")
        return
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = None
        if line.lower().startswith("kernel="):
            name = line.split("=", 1)[1].strip()
        elif line.lower().startswith("initramfs "):
            name = line.split(None, 1)[1].strip()
        if name and not _exists(index, f"{FAT_NAME}/{name}"):
            report.add("ERROR", "config.txt", f"line {number} needs {name}, not on the partition")


def run_checks(manifest: dict, host_files: dict[str, Path]) -> Report:
    report = Report()
    for problem in manifest["problems"]:
        report.add("ERROR" if "skipped" not in problem else "INFO", "image", problem)
    index = {_fold(e["path"]): e for e in manifest["entries"]}
    boot = next(
        (
            p["name"]
            for p in manifest["partitions"]
            if _fold(f"{p['name']}/S/Startup-Sequence") in index
        ),
        None,
    )
    if boot is None:
        report.add("ERROR", "image", "no partition has S/Startup-Sequence")
        return report
    report.add("INFO", "image", f"boot partition {boot}, {len(index)} entries")
    check_scripts(manifest, index, boot, report)
    check_blocks(manifest, boot, report)
    check_receipts(manifest, index, boot, report)
    check_text_files(manifest, host_files, boot, report)
    check_files(manifest, host_files, boot, report)
    check_boot_partition(manifest, index, report)
    return report


# --- compare ------------------------------------------------------------------------------

# keys that hold build time, host or run specific values
_VOLATILE_KEYS = {"created", "built", "timestamp", "time", "date", "host", "build_host", "duration"}


# carries the build time
_STAMPED = {_fold(f"{FAT_NAME}/BUILT-WITH-EMU68HATCHER.txt")}


def _strip_volatile(value):
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def compare(a: dict, b: dict) -> tuple[list[str], int]:
    import difflib

    lines: list[str] = []
    left = {_fold(e["path"]): e for e in a["entries"]}
    right = {_fold(e["path"]): e for e in b["entries"]}
    documents = {_fold(p) for p in [*a["documents"], *b["documents"]]}
    differences = 0
    for key in sorted(set(left) | set(right)):
        x, y = left.get(key), right.get(key)
        if x is None or y is None:
            entry = x or y
            side = "only in A" if y is None else "only in B"
            lines.append(f"{side:12} {entry['type']:4} {entry['path']}")
            differences += 1
            continue
        notes = []
        if x["path"] != y["path"]:
            notes.append(f"name case {x['path']} / {y['path']}")
        if x["type"] != y["type"]:
            notes.append(f"type {x['type']} / {y['type']}")
        if x.get("attrs") != y.get("attrs"):
            notes.append(f"attrs {x.get('attrs')} / {y.get('attrs')}")
        if x.get("comment") != y.get("comment"):
            notes.append("comment")
        if key in documents:
            if _strip_volatile(_document(a, x["path"])) != _strip_volatile(_document(b, y["path"])):
                notes.append("content (beyond time and host fields)")
        elif key not in _STAMPED and x.get("sha256") != y.get("sha256"):
            notes.append(f"content, size {x.get('size')} / {y.get('size')}")
        if notes:
            differences += 1
            lines.append(f"{'differs':12} {x['type']:4} {x['path']}: {'; '.join(notes)}")
    ta = {_fold(k): v for k, v in a["texts"].items()}
    tb = {_fold(k): v for k, v in b["texts"].items()}
    for key in sorted(set(ta) & set(tb)):
        if ta[key] != tb[key]:
            lines.append("")
            lines.extend(
                difflib.unified_diff(
                    ta[key].splitlines(), tb[key].splitlines(), f"A/{key}", f"B/{key}", lineterm=""
                )
            )
    return lines, differences


# --- commands -----------------------------------------------------------------------------


def _inspect(image: str, out: Path, skip: set[str], keep: bool) -> int:
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="image-check-", dir=out if keep else None))
    try:
        manifest, host_files = dump(image, work, skip)
        report = run_checks(manifest, host_files)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    (out / "report.txt").write_text(report.text() + "\n", encoding="utf-8")
    print(report.text(notes=False))
    print(f"report with notes: {out / 'report.txt'}")
    print(f"manifest: {out / 'manifest.json'}")
    return 1 if report.count("ERROR") else 0


def _fetch_tools() -> None:
    from emu68hatcher.builder.host.tools import download_7zip, download_tool, tool_needs_download
    from emu68hatcher.utils.host_tools import find_7z

    if tool_needs_download("hst-imager") and download_tool("hst-imager") is None:
        sys.exit("hst-imager download failed")
    if find_7z() is None and download_7zip() is None:
        sys.exit("7-Zip download failed")


def cmd_build(args) -> int:
    from emu68hatcher.builder.workflow import BuildWorkflow
    from emu68hatcher.config.loader import load_config
    from emu68hatcher.config.schema import BuildConfig, OutputConfig, OutputType

    if args.fetch_tools:
        _fetch_tools()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    if args.no_ags:
        config.ags_import = None
        # the reserved partitions stay, empty
        for part in config.partitions.iter_amiga_partitions() if config.partitions else ():
            part.ags_reservation = None
    image = out / "image.img"
    image.unlink(missing_ok=True)
    config.output = OutputConfig(
        type=OutputType.IMG,
        path=str(image),
        sparse=True,
        flash_target=None,
        verify_after_flash=False,
    )
    config = BuildConfig.model_validate(json.loads(config.model_dump_json()))
    if args.cancel_at:
        return _cancel_at(config, args.cancel_at, image)
    workflow = BuildWorkflow(config)
    result = workflow.build()
    if not result.success:
        print(f"build failed: {result.error}")
        return 2
    # ags partitions are copies of the user's ags image, not build output
    skip = {
        part.device.casefold()
        for part in (config.partitions.iter_amiga_partitions() if config.partitions else ())
        if part.ags_reservation is not None
    }
    status = _inspect(str(image), out, skip, args.keep)
    if not args.keep_image:
        image.unlink(missing_ok=True)
    return status


def _cancel_at(config, stage: str, image: Path) -> int:
    """cancel when the stage starts; pass if the build ends cancelled and leaves nothing behind"""
    from emu68hatcher.builder.workflow import BuildWorkflow

    workflow = None

    def progress(state) -> None:
        if state.stage.value == stage:
            workflow.cancel()

    workflow = BuildWorkflow(config, progress_callback=progress)
    result = workflow.build()
    problems = []
    if result.error != "Build cancelled by user":
        problems.append(f"build ended with {result.error or 'success'}")
    if workflow.work_dir is not None and workflow.work_dir.exists():
        problems.append(f"work dir left: {workflow.work_dir}")
    if image.exists():
        problems.append(f"image left: {image}")
    for problem in problems:
        print(f"FAIL cancel at {stage}: {problem}")
    if not problems:
        print(f"ok   cancel at {stage}")
    return 1 if problems else 0


def cmd_inspect(args) -> int:
    return _inspect(
        args.image, Path(args.out).resolve(), {s.casefold() for s in args.skip}, args.keep
    )


def cmd_compare(args) -> int:
    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    print(f"A: {a['host']}  {a['image']}")
    print(f"B: {b['host']}  {b['image']}")
    lines, differences = compare(a, b)
    print("\n".join(lines))
    print(f"{differences} entries differ")
    return 1 if differences else 0


DOCKERFILE = """\
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libicu72 ca-certificates \\
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "pydantic>=2.0" "jinja2>=3.0" "pyyaml>=6.0" "certifi>=2024.0" \\
    "packaging>=23.0" "cryptography>=42.0"
"""
DOCKER_IMAGE = "emu68hatcher-image-check"


def _host_paths(value, found: set[Path]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _host_paths(item, found)
    elif isinstance(value, list):
        for item in value:
            _host_paths(item, found)
    elif isinstance(value, str) and value.startswith("/") and not value.startswith("/dev/"):
        path = Path(value)
        if path.exists():
            found.add(path if path.is_dir() else path.parent)


def cmd_linux(args) -> int:
    from emu68hatcher.utils.paths import get_downloads_dir, get_extra_packages_dir

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.config).resolve()
    shutil.copyfile(config_path, out / "config.json")
    built = subprocess.run(
        ["docker", "build", "-t", DOCKER_IMAGE, "-"], input=DOCKERFILE.encode(), check=False
    )
    if built.returncode != 0:
        return built.returncode
    mounts: set[Path] = set()
    _host_paths(json.loads(config_path.read_text(encoding="utf-8")), mounts)
    # nested mounts are covered by their parent
    roots = sorted(mounts, key=lambda p: len(p.parts))
    unique: list[Path] = []
    for path in roots:
        if not any(path == r or r in path.parents for r in unique):
            unique.append(path)
    home = "/home/hatcher"
    cmd = ["docker", "run", "--rm", "-e", f"HATCHER_HOME={home}", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    cmd += ["-v", f"{ROOT}:/src:ro", "-v", f"{out}:/out"]
    cmd += ["-v", f"{get_downloads_dir()}:{home}/cache/downloads"]
    cmd += ["-v", f"{DOCKER_IMAGE}-tools:{home}/tools"]
    packages = get_extra_packages_dir()
    if packages.is_dir():
        cmd += ["-v", f"{packages}:{home}/packages:ro"]
    for path in unique:
        cmd += ["-v", f"{path}:{path}:ro"]
    inner = ["python", "/src/scripts/image-check.py", "build", "--config", "/out/config.json"]
    inner += ["--out", "/work", "--fetch-tools"]
    if args.no_ags:
        inner.append("--no-ags")
    # staging and the image stay on the container's own case-sensitive filesystem
    script = (
        f"{' '.join(inner)}; status=$?; "
        "cp /work/manifest.json /work/report.txt /work/buildlog.txt /out/ 2>/dev/null; "
        "exit $status"
    )
    cmd += [DOCKER_IMAGE, "sh", "-c", script]
    return subprocess.run(cmd, check=False).returncode


def _stages():
    from emu68hatcher.builder.stage_registry import PIPELINE_STAGES

    return PIPELINE_STAGES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build a config as a sparse .img, then inspect it")
    build.add_argument("--config", required=True)
    build.add_argument("--out", required=True, help="directory for image, manifest and report")
    build.add_argument("--no-ags", action="store_true", help="drop the AGS import")
    build.add_argument("--keep", action="store_true", help="keep the extracted files")
    build.add_argument("--keep-image", action="store_true")
    build.add_argument("--fetch-tools", action="store_true", help="download hst-imager and 7-Zip")
    build.add_argument(
        "--cancel-at",
        # flash does nothing for an image-only build
        choices=[s.stage.value for s in _stages() if s.stage.value != "flash"],
        help="cancel when this stage starts and check the build cleans up",
    )
    build.set_defaults(func=cmd_build)

    inspect = sub.add_parser("inspect", help="list and check an existing image or card")
    inspect.add_argument("image", help=".img file or raw disk (/dev/diskN, \\\\.\\PhysicalDriveN)")
    inspect.add_argument("--out", required=True)
    inspect.add_argument("--skip", action="append", default=[], help="device not to read")
    inspect.add_argument("--keep", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    comp = sub.add_parser("compare", help="compare two manifest.json files")
    comp.add_argument("a")
    comp.add_argument("b")
    comp.set_defaults(func=cmd_compare)

    linux = sub.add_parser("linux", help="run 'build' in a linux container (docker)")
    linux.add_argument("--config", required=True)
    linux.add_argument("--out", required=True)
    linux.add_argument("--no-ags", action="store_true")
    linux.set_defaults(func=cmd_linux)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
