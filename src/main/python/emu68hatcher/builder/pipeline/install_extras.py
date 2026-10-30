"""mirror per-partition extra_content_directory into staging/<device>/"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.staging.tree_copy import (
    TargetResolver,
    TreeUsage,
    copy_contained_tree,
    measure_contained_tree,
)
from emu68hatcher.builder.staging.uaefsdb import DB_NAME, import_records
from emu68hatcher.builder.state import BuildStage, CreatedImage
from emu68hatcher.config.partition_helpers import usable_partition_content_size
from emu68hatcher.config.partition_models import AmigaPartition
from emu68hatcher.data.catalog import validate_relative_path

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def _format_size(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GiB"
    return f"{size / 1024**2:.1f} MiB"


def _check_partition_space(
    part: AmigaPartition,
    usage: TreeUsage,
    description: str,
) -> None:
    usable = usable_partition_content_size(part.size)
    if usage.estimated_bytes <= usable:
        return
    raise BuildError(
        f"Extra content does not fit on {part.device} ({part.volume}): {description} need "
        f"about {_format_size(usage.estimated_bytes)}, but only {_format_size(usable)} is "
        f"available on the {_format_size(part.size)} partition. Remove files or enlarge "
        "the partition."
    )


def _amiga_target_resolver(part: AmigaPartition, src: Path) -> TargetResolver:
    """case-insensitive staging resolver that rejects names an Amiga volume cannot hold"""

    def resolve(parent: Path, name: str) -> Path:
        try:
            validate_relative_path(name)
        except ValueError as e:
            raise BuildError(
                f"Extra content for {part.device} ({part.volume}) contains {name!r} in {src}, "
                "a name an Amiga volume cannot hold. Rename it without ':', '\\' or control "
                "characters and build again."
            ) from e
        return resolve_staging_path(parent, name)

    return resolve


def _collect_database(found: list[tuple[Path, Path]], item: Path, target_dir: Path) -> bool:
    # copied raw, a UaeFsDb from an emulator folder would replace the staging one
    if item.name.casefold() != DB_NAME.casefold():
        return False
    found.append((item, target_dir))
    return True


def stage_install_extras(workflow: BuildWorkflow, image: CreatedImage) -> CreatedImage:
    """copy each amiga partitions extra_content_directory contents into staging/<device>/"""
    workflow._update_state(BuildStage.INSTALL_EXTRAS, 0.0)
    workflow._milestone("Mirroring per-partition extra content")

    if not workflow.config.partitions:
        workflow._update_state(progress=100.0)
        workflow._milestone("No partitions configured - nothing to mirror")
        return image

    parts = [
        p
        for p in workflow.config.partitions.iter_amiga_partitions()
        if p.extra_content_directory is not None
    ]
    if not parts:
        workflow._update_state(progress=100.0)
        workflow._milestone("No per-partition extras configured")
        return image

    total_files = 0
    for i, part in enumerate(parts):
        workflow._check_cancelled()
        src = Path(part.extra_content_directory).expanduser()

        if not src.exists() or not src.is_dir():
            workflow.logger.warning(
                f"extra_content_directory for {part.device} ({part.volume}) "
                f"not found or not a directory: {src}"
            )
            continue

        try:
            source_usage = measure_contained_tree(src)
        except OSError as e:
            raise BuildError(f"Cannot read extra content directory for {part.device}: {e}") from e
        _check_partition_space(part, source_usage, "the selected directory")

        dest = image.workspace.staging_dir / part.device
        dest.mkdir(parents=True, exist_ok=True)

        # user content wins on collision (intentional - "put my files in the image");
        # the receipts mark package files replaced here as locally modified
        log = workflow.write_log
        on_file = log.extra_wrote if log and part.device == workflow.config.boot_device else None
        databases: list[tuple[Path, Path]] = []
        result = copy_contained_tree(
            src,
            dest,
            on_file=on_file,
            resolve_target=_amiga_target_resolver(part, src),
            skip_file=partial(_collect_database, databases),
        )
        for database, target_dir in databases:
            try:
                import_records(database, target_dir)
            except (BuildError, ValueError, OSError) as e:
                workflow.logger.warning(f"Ignoring Amiga file metadata {database}: {e}")
        staged_usage = measure_contained_tree(dest)
        _check_partition_space(part, staged_usage, "the staged files")
        total_files += result.files_copied
        workflow.logger.info(
            f"Mirrored {result.files_copied} files from {src} -> staging/{part.device}/"
        )
        workflow.logger.info(
            f"Partition space check for {part.device}: about "
            f"{_format_size(staged_usage.estimated_bytes)} used, "
            f"{_format_size(usable_partition_content_size(part.size))} usable"
        )
        if result.skipped_cycles:
            workflow.logger.warning(
                f"Skipped {result.skipped_cycles} repeated directories while copying {src}"
            )
        if result.skipped_outside:
            workflow.logger.warning(
                f"Skipped {result.skipped_outside} paths outside the extras root {src}"
            )

        progress = ((i + 1) / len(parts)) * 100
        workflow._update_state(progress=progress)

    workflow._update_state(progress=100.0)
    workflow._milestone(f"Extras mirrored ({total_files} files across {len(parts)} partition(s))")
    return image
