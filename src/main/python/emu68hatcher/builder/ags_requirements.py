"""Exact AGS partition sizes and small launcher staging allowance."""

from collections.abc import Iterable
from dataclasses import dataclass

from emu68hatcher.config.ags_models import AGSRole


@dataclass(frozen=True, slots=True)
class AGSRoleRequirement:
    role: AGSRole
    content_bytes: int
    entry_count: int
    minimum_partition_bytes: int
    estimated_host_bytes: int


def calculate_ags_requirements(components: Iterable[object]) -> tuple[AGSRoleRequirement, ...]:
    return tuple(
        AGSRoleRequirement(
            component.role, component.partition.size, 0, component.partition.size, 16 * 1024**2
        )
        for component in components
    )
