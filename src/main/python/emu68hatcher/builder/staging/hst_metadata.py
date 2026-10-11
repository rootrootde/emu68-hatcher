"""Prepare HST sidecars without losing Amiga filenames."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from emu68hatcher.builder.ags_metadata import (
    _DB_NAME,
    _records_by_host,
    coalesce_staged_directories,
    iter_staged_entries,
)
from emu68hatcher.builder.errors import BuildCancelledError, BuildError

_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)")
_SPECIAL = frozenset('\\/:*?"<>|#%')


def _encoded_name(name: str) -> str:
    if any(ord(char) > 255 for char in name):
        raise BuildError(f"Filename cannot be represented in the Amiga character set: {name}")
    encoded = "".join(
        f"%{ord(char):02x}"
        if char in _SPECIAL
        or not 32 <= ord(char) <= 126
        or (index == len(name) - 1 and char in ". ")
        else char
        for index, char in enumerate(name)
    )
    if _RESERVED.match(encoded):
        encoded = f"%{ord(encoded[0]):02x}" + encoded[1:]
    # a real .uaem file must not be mistaken for a transport sidecar
    if encoded.lower().endswith(".uaem"):
        encoded = encoded[:-1] + "%6d"
    if len(encoded.encode("utf-8")) + len(".uaem") > 255:
        raise BuildError(f"Workbench metadata filename is too long for host staging: {name}")
    return encoded


def _sidecar(bits: int, comment: str, mtime: float) -> bytes:
    flags = bits ^ 15
    permissions = "".join(
        char if flags & (1 << (7 - index)) else "-" for index, char in enumerate("hsparwed")
    )
    timestamp = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S.%f")[:-4]
    return f"{permissions} {timestamp} {comment}\n".encode("iso-8859-1")


def _transport_name(amiga_name: str, amiga_names: set[str]) -> str:
    name = _encoded_name(amiga_name)
    if name == amiga_name:
        return name
    position = 0
    # HST looks up host and Amiga names in the same directory caches
    while name.casefold() in amiga_names:
        while position < len(name) and name[position] == "%":
            position += 3
        if position >= len(name):
            raise BuildError(f"Cannot disambiguate Workbench transport filename: {amiga_name}")
        name = name[:position] + f"%{ord(name[position]):02x}" + name[position + 1 :]
        position += 3
        if len(name) + len(".uaem") > 255:
            raise BuildError(
                f"Workbench metadata filename is too long for host staging: {amiga_name}"
            )
    return name


def prepare_workbench_metadata(
    root: Path,
    cancel_check: Callable[[], bool] | None = None,
    *,
    allow_stale_metadata: bool = False,
) -> None:
    # HST 1.6.616 reads later UaeFsDb records incorrectly; sidecars avoid that reader
    # Workbench extraction leaves records for decompressed .Z files and moved icons
    if allow_stale_metadata:
        coalesce_staged_directories(root, cancel_check)
    for entry in iter_staged_entries(root, cancel_check, allow_stale_metadata=allow_stale_metadata):
        _encoded_name(entry.path.rsplit("/", 1)[-1])
    stack = [root]
    while stack:
        if cancel_check and cancel_check():
            raise BuildCancelledError("Build was cancelled by user")
        parent = stack.pop()
        records = _records_by_host(parent)
        with os.scandir(parent) as children:
            entries = [entry for entry in children if entry.name != _DB_NAME]
        present = {entry.name.casefold() for entry in entries}
        for key in records.keys() - present:
            logging.getLogger(__name__).debug(
                "Ignoring obsolete Workbench metadata: %s/%s", parent, records[key].host_name
            )
        planned = []
        names: set[str] = set()
        amiga_names = {
            (
                records[entry.name.casefold()].amiga_name
                if entry.name.casefold() in records
                else entry.name
            ).casefold()
            for entry in entries
        }
        for index, entry in enumerate(entries):
            if index % 256 == 0 and cancel_check and cancel_check():
                raise BuildCancelledError("Build was cancelled by user")
            record = records.get(entry.name.casefold())
            amiga_name = record.amiga_name if record else entry.name
            name = _transport_name(amiga_name, amiga_names)
            if name.casefold() in names or (name + ".uaem").casefold() in names:
                raise BuildError(
                    f"Workbench metadata transport name collision in {parent}: {amiga_name}"
                )
            names.update((name.casefold(), (name + ".uaem").casefold()))
            info = entry.stat(follow_symlinks=False)
            planned.append((entry, name, record, info, entry.is_dir(follow_symlinks=False)))
        # move changed names aside first, so swaps cannot overwrite another entry
        pending = []
        for entry, name, record, info, is_dir in planned:
            source = parent / entry.name
            if entry.name != name:
                descriptor, temporary = tempfile.mkstemp(prefix=".metadata-name-", dir=parent)
                os.close(descriptor)
                os.unlink(temporary)
                source.rename(temporary)
                source = Path(temporary)
            pending.append((source, name, record, info, is_dir))
        for index, (source, name, record, info, is_dir) in enumerate(pending):
            if index % 256 == 0 and cancel_check and cancel_check():
                raise BuildCancelledError("Build was cancelled by user")
            destination = parent / name
            if source != destination:
                source.rename(destination)
            # sidecars also carry the reversible filename encoding, including zero flags
            if record is not None or "%" in name:
                sidecar = parent / (name + ".uaem")
                sidecar.write_bytes(
                    _sidecar(
                        record.protection_bits if record else 0,
                        record.comment if record else "",
                        info.st_mtime,
                    )
                )
            if is_dir:
                stack.append(destination)
        database = parent / _DB_NAME
        if database.exists():
            database.unlink()
