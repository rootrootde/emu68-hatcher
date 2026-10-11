"""Rule-driven ADF extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.adf_mapping import build_adf_name_map, resolve_adf_path
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.state import Workspace

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow


def extract_adfs_with_rules(
    workflow: BuildWorkflow,
    workspace: Workspace,
    adf_paths: list[Path],
) -> tuple[int, list[str]]:
    from emu68hatcher.utils.host_tools import find_hst_imager

    hst_imager = find_hst_imager()
    if not hst_imager:
        raise BuildError(
            "HST Imager not found. Open the Start tab and download the required tools."
        )
    mapping = build_adf_name_map(
        workflow,
        adf_paths,
        workspace.validated.resolved_install_media,
    )
    return _run_rules(workflow, workspace, _resolve_rules(workflow), mapping, hst_imager)


def _resolve_rules(workflow: BuildWorkflow) -> list:
    from emu68hatcher.builder.pipeline._selection import get_resolution
    from emu68hatcher.data.package_loader import get_filtered_adf_rules

    kickstart_version = workflow.config.kickstart.version.value
    enabled = get_resolution(workflow).selected
    workflow.logger.debug(f"Enabled packages for ADF rules: {enabled}")
    rules = get_filtered_adf_rules(
        kickstart_version,
        enabled,
        workflow.config.icon_set or "Default",
    )
    workflow.logger.info(
        f"Found {len(rules)} ADF extraction rules for Kickstart {kickstart_version}"
    )
    return rules


def _run_rules(
    workflow: BuildWorkflow,
    workspace: Workspace,
    rules: list,
    mapping: dict[str, Path],
    hst_imager: Path,
) -> tuple[int, list[str]]:
    from emu68hatcher.utils.host_tools import localize_for_hst, run_hst_extract

    errors: list[str] = []
    processed = 0
    last_adf: str | None = None
    for rule in rules:
        workflow._check_cancelled()
        adf_path = resolve_adf_path(mapping, rule.adf)
        if not adf_path:
            if rule.mandatory:
                workflow.logger.warning(
                    f"Missing ADF for mandatory rule: {rule.adf} (need {rule.source})"
                )
            continue
        try:
            adf_path = localize_for_hst(
                adf_path,
                workspace.extracted_dir / "network_media",
            )
        except OSError as e:
            errors.append(f"{rule.adf}: copy from network path failed: {e}")
            continue

        dest_dir = resolve_staging_path(workspace.workbench_dir, rule.dest.rstrip("/"))
        dest_dir.mkdir(parents=True, exist_ok=True)
        source_path = f"{adf_path.as_posix()}/{rule.source}" if rule.source else adf_path.as_posix()
        dest_path = (
            str(resolve_staging_path(dest_dir, rule.rename))
            if rule.rename
            else dest_dir.as_posix() + "/"
        )
        try:
            result = run_hst_extract(
                hst_imager,
                source_path,
                dest_path,
                uaemetadata="UaeFsDb",
                recursive=rule.recursive,
            )
            if result.returncode == 0:
                processed += 1
                if rule.rename:
                    _normalize_rename_case(Path(dest_path))
            elif "not found" not in (result.stdout + result.stderr).lower():
                errors.append(f"{rule.adf}/{rule.source}: {result.stderr or result.stdout}")
            elif rule.mandatory:
                workflow.logger.warning(
                    f"Extraction failed for mandatory rule {rule.adf}/{rule.source}"
                )
        except subprocess.TimeoutExpired:
            errors.append(f"{rule.adf}/{rule.source}: Timeout")
        except (OSError, subprocess.SubprocessError) as e:
            errors.append(f"{rule.adf}/{rule.source}: {e}")

        workflow._update_state(
            progress=10.0 + 40.0 * processed / max(len(rules), 1),
            message=f"Extracting {rule.adf}: {rule.source}",
        )
        if rule.adf != last_adf:
            workflow._milestone(f"Extracting from {rule.adf}")
            last_adf = rule.adf

    total_files = sum(1 for path in workspace.workbench_dir.rglob("*") if path.is_file())
    workflow.logger.info(f"Extracted {total_files} files from ADFs using {processed} YAML rules")
    return total_files, errors


def _normalize_rename_case(target: Path) -> None:
    if target.exists() or not target.parent.is_dir():
        return
    target_lower = target.name.lower()
    for sibling in target.parent.iterdir():
        if sibling.is_file() and sibling.name.lower() == target_lower:
            sibling.rename(target)
            return
