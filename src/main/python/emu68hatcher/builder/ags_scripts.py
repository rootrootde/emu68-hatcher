"""Prepare assigns for the original AGS portable launcher."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .ags_profiles import AGSProfile, get_profile
from .errors import BuildError
from .staging.files import resolve_staging_path
from .staging.scripts.injector import InjectionAction, ScriptInjection, inject_script


@dataclass(frozen=True, slots=True)
class AGSScriptPlan:
    profile: str
    selected_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AGSLauncherSummary:
    verified_files: tuple[tuple[str, str, str], ...]


def required_script_paths(profile: str | AGSProfile) -> tuple[str, ...]:
    profile = get_profile(profile)
    return tuple(profile.marker_hashes)


def prepare_ags_script_plan(profile, selected_roles, source_scripts: Mapping[str, bytes]):
    profile = get_profile(profile)
    if "whdload" not in selected_roles or set(selected_roles) - {
        "whdload",
        "games",
        "work",
        "media",
    }:
        raise BuildError("AGS script selection has invalid roles")
    for path, digest in profile.marker_hashes.items():
        if path not in source_scripts or hashlib.sha256(source_scripts[path]).hexdigest() != digest:
            raise BuildError(f"AGS portable profile differs at {path}")
    return AGSScriptPlan(profile.name, tuple(selected_roles))


def prepare_ags_launcher(plan, boot_root: Path, cancel_check=None) -> AGSLauncherSummary:
    from .ags_source import _check_cancel

    roles = set(plan.script_plan.selected_roles)
    whd = plan.target("whdload")
    verified = [
        (whd.device, relative, digest)
        for relative, digest in get_profile(plan.inventory.profile).marker_hashes.items()
    ]

    aliases = {
        "WHD_Games": f"{whd.device}:",
        "WHD_Demos": f"{whd.device}:",
        "AGS_Drive": f"{whd.device}:",
        "AGS": f"{whd.device}:AGS2",
        "AGSOS": "AGS:OS",
        "Scripts": "AGS:Scripts",
        "Admin": "Scripts:Admin",
    }
    if "games" in roles:
        aliases["Premium"] = f"{plan.target('games').device}:Premium"
    if "work" in roles:
        aliases["Emulators"] = f"{plan.target('work').device}:Emulators"
    if "media" in roles:
        aliases["ST-00"] = f"{plan.target('media').device}:ST-00"
    setup = [f'Assign {name}: "{target}"' for name, target in aliases.items()]
    setup += [
        "Path AGSOS:C ADD",
        "Assign Libs: AGSOS:Libs ADD",
        "Assign Fonts: AGSOS:Fonts ADD",
    ]
    _check_cancel(cancel_check)
    user_startup = resolve_staging_path(boot_root, "S/User-Startup")
    result = inject_script(
        user_startup,
        ScriptInjection(
            target_script="S/User-Startup",
            action=InjectionAction.ADD,
            content="\n".join(setup),
            name="AGS assigns",
        ),
    )
    if result.error or not result.matched:
        raise BuildError(f"Could not add AGS assigns to S:User-Startup: {result.error}")
    verified.append(
        ("__boot__", "S/User-Startup", hashlib.sha256(user_startup.read_bytes()).hexdigest())
    )
    return AGSLauncherSummary(tuple(verified))
