"""Share AGS source inspection across the GUI and builder."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType

from emu68hatcher.builder.ags_source import (
    AGSComponentInventory,
    AGSInventory,
    CancelCheck,
    _check_cancel,
    _volume,
    inspect_ags_component,
    inspect_ags_partitions,
    read_ags_scripts,
    source_identity,
)
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.ags_models import AGS_ROLES, AGSRole
from emu68hatcher.utils.host_tools import find_hst_imager

_PARSER_REVISION = 3
_MAX_SOURCES = 2
_cache_lock = threading.Lock()
_source_locks: dict[tuple[int, int], threading.RLock] = {}
_cache: OrderedDict[tuple, AGSInventory] = OrderedDict()


def _warnings(profile: str, roles: tuple[AGSRole, ...]) -> tuple[str, ...]:
    if "games" not in roles:
        return ()
    warnings = [
        "Some Premium titles need separately supplied commercial game data; see the Premium help."
    ]
    if profile == "v31-beta-160726":
        warnings.append(
            "The known v31 source lacks StuntCarRacer50, BoingBallDemo and BadApple; "
            "their original starters remain, but cannot run without those data."
        )
    return tuple(warnings)


@contextmanager
def ags_source_lock(source_path: Path, cancel_check: CancelCheck = None) -> Iterator[None]:
    identity = source_identity(Path(source_path))
    key = identity[:2]
    with _cache_lock:
        lock = _source_locks.setdefault(key, threading.RLock())
    while True:
        _check_cancel(cancel_check)
        if lock.acquire(timeout=0.2):
            break
    try:
        _check_cancel(cancel_check)
        yield
    finally:
        lock.release()


def _cache_key(path: Path) -> tuple:
    binary = find_hst_imager()
    if binary is None or not binary.is_file():
        raise BuildError("HST Imager is needed to inspect the AGS image")
    stamp = binary.with_name(binary.name + ".version")
    binary_stat = binary.stat()
    profile_file = Path(__file__).with_name("ags_profiles.py")
    profile_stat = profile_file.stat() if profile_file.exists() else None
    return (
        str(path),
        *source_identity(path),
        _PARSER_REVISION,
        profile_stat.st_mtime_ns if profile_stat else 0,
        str(binary.resolve()),
        binary_stat.st_size,
        binary_stat.st_mtime_ns,
        stamp.read_text(encoding="utf-8") if stamp.is_file() else "",
    )


def _lookup(key: tuple, roles: tuple[AGSRole, ...]) -> AGSInventory | None:
    with _cache_lock:
        inventory = _cache.get(key)
        if inventory is None:
            return None
        if not set(roles) <= {component.role for component in inventory.components}:
            return inventory
        _cache.move_to_end(key)
        return AGSInventory(
            inventory.source_path,
            inventory.identity,
            inventory.profile,
            inventory.version,
            inventory.partitions,
            tuple(component for component in inventory.components if component.role in roles),
            inventory.marker_hashes,
            _warnings(inventory.profile, roles),
            inventory.source_scripts,
        )


def _publish(key: tuple, inventory: AGSInventory) -> None:
    with _cache_lock:
        _cache[key] = inventory
        _cache.move_to_end(key)
        while len(_cache) > _MAX_SOURCES:
            _cache.popitem(last=False)


def inspect_ags_source(
    path: Path,
    roles: tuple[AGSRole, ...] | list[AGSRole],
    cancel_check: CancelCheck = None,
    refresh: bool = False,
) -> AGSInventory:
    from emu68hatcher.builder.ags_profiles import AGS_PROFILES, match_profile
    from emu68hatcher.builder.ags_scripts import required_script_paths

    if str(path).startswith(("\\\\", "//")):
        raise BuildError("AGS source must be a local image file; UNC paths are unsupported")
    path = Path(path).expanduser().resolve()
    selected = tuple(role for role in AGS_ROLES if role in roles)
    if not selected or "whdload" not in selected or len(selected) != len(roles):
        raise BuildError("AGS inspection needs WHDLoad and valid unique selected roles")
    with ags_source_lock(path, cancel_check):
        _check_cancel(cancel_check)
        key = _cache_key(path)
        if refresh:
            with _cache_lock:
                for old_key in tuple(_cache):
                    if old_key[0] == str(path):
                        _cache.pop(old_key)
        cached = _lookup(key, selected)
        if cached and set(selected) == {component.role for component in cached.components}:
            if _cache_key(path) != key:
                raise BuildError("AGS source image or inspector changed during inspection")
            return cached
        if cached is None:
            partitions = inspect_ags_partitions(path, cancel_check)
            whdload = _volume(partitions, "WHDLoad")
            scripts = read_ags_scripts(
                path, whdload, tuple(next(iter(AGS_PROFILES.values())).marker_hashes), cancel_check
            )
            marker_hashes = MappingProxyType(
                {name: hashlib.sha256(data).hexdigest() for name, data in scripts.items()}
            )
            profile = match_profile(marker_hashes)
            extra_paths = tuple(
                name for name in required_script_paths(profile) if name not in scripts
            )
            if extra_paths:
                scripts = MappingProxyType(
                    dict(scripts) | dict(read_ags_scripts(path, whdload, extra_paths, cancel_check))
                )
            components: dict[AGSRole, AGSComponentInventory] = {}
            warnings = _warnings(profile.name, selected)
        else:
            partitions = cached.partitions
            marker_hashes = cached.marker_hashes
            scripts = cached.source_scripts
            profile = match_profile(marker_hashes)
            components = {component.role: component for component in cached.components}
            warnings = _warnings(profile.name, selected)
        for role in selected:
            if role not in components:
                components[role] = inspect_ags_component(path, partitions, role, cancel_check)
        _check_cancel(cancel_check)
        if _cache_key(path) != key:
            raise BuildError("AGS source image or inspector changed during inspection")
        full = AGSInventory(
            path,
            key[1:6],
            profile.name,
            profile.version,
            partitions,
            tuple(components[role] for role in AGS_ROLES if role in components),
            marker_hashes,
            warnings,
            MappingProxyType(dict(scripts)),
        )
        _publish(key, full)
        return AGSInventory(
            path,
            full.identity,
            full.profile,
            full.version,
            full.partitions,
            tuple(components[role] for role in selected),
            full.marker_hashes,
            full.warnings,
            full.source_scripts,
        )
