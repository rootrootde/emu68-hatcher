"""Plan and validate AGS partition reservations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from emu68hatcher.config.ags_models import AGS_ROLES, AGSPartitionReservation, AGSRole
from emu68hatcher.config.constants import (
    CYLINDER_SIZE,
    MAX_AMIGA_PARTITIONS,
    PFS3_MAX_PARTITION_SIZE,
)
from emu68hatcher.config.partition_helpers import (
    calculate_free_space,
    next_device_name,
)
from emu68hatcher.config.partition_models import AmigaPartition, Filesystem, PartitionConfig

if TYPE_CHECKING:
    from emu68hatcher.config.schema import BuildConfig

AGS_VOLUMES: Mapping[AGSRole, str] = {
    "whdload": "WHDLoad",
    "games": "Games",
    "work": "Work",
    "media": "Media",
}
AGS_ASSIGN_NAMES = frozenset(
    {
        "whd_games",
        "whd_demos",
        "ags_drive",
        "ags",
        "agsos",
        "scripts",
        "admin",
        "whdsaves",
        "premium",
        "emulators",
        "st-00",
    }
)


@dataclass(frozen=True, slots=True)
class AGSResolvedTarget:
    role: AGSRole
    device: str
    volume: str
    partition: AmigaPartition


@dataclass(frozen=True, slots=True)
class AGSLayoutPlan:
    layout: PartitionConfig | None
    partitions: tuple[AmigaPartition, ...]
    free_bytes: int
    errors: tuple[str, ...]


def plan_ags_layout(
    layout: PartitionConfig,
    requirements: Mapping[AGSRole, int],
    selected_roles: tuple[AGSRole, ...] | list[AGSRole],
) -> AGSLayoutPlan:
    """Reserve the selected AGS partitions; layout is None when errors remain."""
    selected = tuple(role for role in AGS_ROLES if role in selected_roles)
    errors: list[str] = []
    if len(selected_roles) != len(selected) or len(set(selected_roles)) != len(selected_roles):
        errors.append("AGS selection has an unknown or duplicate role")
    if selected and "whdload" not in selected:
        errors.append("AGS requires WHDLoad when import is enabled")
    parts = [part.model_copy(deep=True) for part in layout.iter_amiga_partitions()]
    parts = [
        part for part in parts if not part.ags_reservation or part.ags_reservation.role in selected
    ]
    existing_roles: dict[AGSRole, AmigaPartition] = {}
    for part in parts:
        reservation = part.ags_reservation
        if reservation:
            if reservation.role in existing_roles:
                errors.append(f"AGS role {reservation.role} has more than one partition")
            existing_roles[reservation.role] = part
    volume_names = {part.volume.casefold() for part in parts if not part.ags_reservation}
    for part in parts:
        if (
            selected
            and not part.ags_reservation
            and {part.device.casefold(), part.volume.casefold()} & AGS_ASSIGN_NAMES
        ):
            errors.append(f"AGS assign conflicts with partition {part.device}: {part.volume}")
    devices = [part.device for part in parts]
    for role in selected:
        try:
            minimum = requirements[role]
        except KeyError as exc:
            errors.append(f"No size requirement for AGS {role}: {exc}")
            continue
        if minimum <= 0 or minimum % CYLINDER_SIZE or minimum > PFS3_MAX_PARTITION_SIZE:
            errors.append(f"AGS {role} has an invalid PFS3 minimum: {minimum}")
            continue
        current = existing_roles.get(role)
        if current:
            if current.volume != AGS_VOLUMES[role]:
                errors.append(f"AGS {role} must use volume {AGS_VOLUMES[role]}")
            current.ags_reservation = AGSPartitionReservation(role=role, minimum_size=minimum)
            current.size = minimum
            continue
        volume = AGS_VOLUMES[role]
        if volume.casefold() in volume_names:
            errors.append(f"AGS volume {volume} conflicts with an existing partition")
            continue
        device = next_device_name(devices)
        devices.append(device)
        parts.append(
            AmigaPartition(
                device=device,
                volume=volume,
                filesystem=Filesystem.PFS3,
                size=minimum,
                ags_reservation=AGSPartitionReservation(role=role, minimum_size=minimum),
            )
        )
    free = calculate_free_space(layout.layout[1].size, parts)
    if free < 0:
        errors.append(
            f"AGS layout exceeds RDB capacity by {-free} bytes; deselect content, "
            "choose a larger target, or adjust your partitions"
        )
    if len(parts) > MAX_AMIGA_PARTITIONS:
        errors.append(f"AGS layout exceeds the {MAX_AMIGA_PARTITIONS}-partition limit")
    planned = None
    if not errors:
        try:
            planned = _layout_with_parts(layout, parts)
        except ValidationError as exc:
            errors.extend(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        except ValueError as exc:
            errors.append(str(exc))
    return AGSLayoutPlan(planned, tuple(parts), free, tuple(errors))


def _layout_with_parts(layout: PartitionConfig, parts: list[AmigaPartition]) -> PartitionConfig:
    result = layout.model_copy(deep=True)
    result.layout[1].amiga_partitions = parts
    return PartitionConfig.model_validate(result.model_dump())


def resolve_ags_targets(config: BuildConfig) -> tuple[AGSResolvedTarget, ...]:
    if config.partitions is None:
        return ()
    targets = {}
    for part in config.partitions.iter_amiga_partitions():
        if part.ags_reservation:
            role = part.ags_reservation.role
            if role in targets:
                raise ValueError(f"AGS role {role} has more than one partition")
            targets[role] = AGSResolvedTarget(role, part.device, part.volume, part)
    return tuple(targets[role] for role in AGS_ROLES if role in targets)


def validate_ags_layout(
    config: BuildConfig, requirements: Mapping[AGSRole, int] | None = None
) -> tuple[str, ...]:
    selection = config.ags_import
    parts = tuple(config.partitions.iter_amiga_partitions()) if config.partitions else ()
    reserved = tuple(part for part in parts if part.ags_reservation)
    if selection is None or not selection.enabled:
        return ("Inactive AGS import has reserved partitions",) if reserved else ()
    errors = []
    if selection.allocation_state != "ready":
        errors.append(
            "AGS layout is not ready; check the source and available space in the AGS tab"
        )
    if config.partitions is None:
        errors.append("AGS import requires a partition layout")
        return tuple(errors)
    selected = selection.components.selected_roles()
    counts = dict.fromkeys(AGS_ROLES, 0)
    for part in reserved:
        reservation = part.ags_reservation
        counts[reservation.role] += 1
        if reservation.role not in selected:
            errors.append(f"AGS {reservation.role} is reserved but not selected")
        if part.volume != AGS_VOLUMES[reservation.role]:
            errors.append(f"AGS {reservation.role} must use volume {AGS_VOLUMES[reservation.role]}")
        if part.filesystem != Filesystem.PFS3 or part.bootable or part.no_mount:
            errors.append(f"AGS {reservation.role} requires mounted, non-bootable PFS3")
        if part.extra_content_directory is not None:
            errors.append(f"AGS {reservation.role} cannot contain extra host files")
        minimum = reservation.minimum_size
        if requirements is not None:
            try:
                minimum = requirements[reservation.role]
            except KeyError:
                errors.append(f"No current size requirement for AGS {reservation.role}")
        if part.size != minimum or reservation.minimum_size != minimum:
            errors.append(
                f"AGS {reservation.role} partition {part.device} must match the source size exactly"
            )
    for role in selected:
        if counts[role] != 1:
            errors.append(f"AGS {role} requires exactly one reserved partition")
    for part in parts:
        if (
            not part.ags_reservation
            and {part.device.casefold(), part.volume.casefold()} & AGS_ASSIGN_NAMES
        ):
            errors.append(f"AGS assign conflicts with partition {part.device}: {part.volume}")
    return tuple(errors)
