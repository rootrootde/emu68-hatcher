"""Rule-driven ADF extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.adf_mapping import build_adf_name_map, resolve_adf_path
from emu68hatcher.builder.staging.files import resolve_staging_path
from emu68hatcher.builder.staging.uaefsdb import drop_superseded_records
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


def extract_ffs_handler(workflow: BuildWorkflow, workspace: Workspace) -> Path | None:
    """extract L/FastFileSystem from the Install ADF if any partition uses FFS"""
    if not workflow.config.partitions or not workflow.config.partitions.uses_ffs:
        return None

    from emu68hatcher.utils.host_tools import find_hst_imager, localize_for_hst, run_hst_extract
    from emu68hatcher.utils.paths import ensure_dir

    install_adf = _find_install_adf(workflow, workspace)
    if install_adf is None or not install_adf.exists():
        dirs_str = ", ".join(str(d) for d in workflow.config.asset_directories) or "(none)"
        raise BuildError(
            "FFS partition selected but no Install ADF found to extract "
            f"L/FastFileSystem from. Asset directories: {dirs_str}. "
            "Place an Install3.x.adf in one of them."
        )

    hst_imager = find_hst_imager()
    if not hst_imager:
        raise BuildError("hst-imager not available; cannot extract FFS handler")

    try:
        install_adf = localize_for_hst(install_adf, workspace.extracted_dir / "network_media")
    except OSError as e:
        raise BuildError(f"Cannot copy {install_adf.name} from network path: {e}") from e

    scratch = ensure_dir(workspace.extracted_dir / "ffs_handler")
    workflow._milestone("Extracting FFS handler from Install ADF")
    result = run_hst_extract(
        hst_imager,
        f"{install_adf.as_posix()}/L/FastFileSystem",
        scratch.as_posix() + "/",
        uaemetadata="UaeFsDb",
    )
    handler = scratch / "FastFileSystem"
    if result.returncode != 0 or not handler.exists():
        raise BuildError(
            f"Failed to extract L/FastFileSystem from {install_adf.name}: "
            f"{result.stderr.strip() or result.stdout.strip() or 'handler not found'}"
        )
    workflow.logger.info(f"FFS handler ready: {handler}")
    return handler


def _find_install_adf(workflow: BuildWorkflow, workspace: Workspace) -> Path | None:
    from emu68hatcher.data.package_loader import get_adf_rules_for_version

    kickstart_version = workflow.config.kickstart.version.value
    names = sorted(
        {
            rule.adf
            for rule in get_adf_rules_for_version(kickstart_version)
            if rule.adf.lower().startswith("install")
        }
    )
    mapping = build_adf_name_map(workflow, [], workspace.validated.resolved_install_media)
    for name in names:
        path = resolve_adf_path(mapping, name)
        if path is not None:
            return path

    # filename fallback for Install ADFs the hash database does not know (sorted for determinism)
    for directory in (Path(p) for p in workflow.config.asset_directories):
        if not directory.exists():
            continue
        candidates = sorted(
            p for p in directory.rglob("*.adf") if p.name.lower().startswith("install")
        )
        if candidates:
            return candidates[0]
    return None


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
    missing: dict[str, list] = {}
    for rule in rules:
        if not resolve_adf_path(mapping, rule.adf):
            missing.setdefault(rule.adf, []).append(rule)
    for adf, skipped in missing.items():
        prefix = "Mandatory " if any(r.mandatory for r in skipped) else ""
        workflow.logger.warning(f"{prefix}ADF {adf} not found: {len(skipped)} rule(s) skipped")
    for rule in rules:
        workflow._check_cancelled()
        adf_path = resolve_adf_path(mapping, rule.adf)
        if not adf_path:
            workflow.logger.debug(
                f"skipped {rule.adf}/{rule.source or '/'} -> {rule.dest}: ADF not found"
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
            else:
                kind = "Mandatory ADF rule" if rule.mandatory else "ADF rule"
                hst_lines = [ln.strip() for ln in (result.stderr + result.stdout).splitlines()]
                hst_msg = next((ln for ln in hst_lines if "not found" in ln.lower()), "")
                workflow.logger.warning(
                    f"{kind} skipped: {rule.adf}/{rule.source} not in the ADF: {hst_msg}"
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

    # e.g. 3.2 Locale-PL and 3.2.3 Locale-PL spell topaz_iso-8859-2 differently
    drop_superseded_records(workspace.workbench_dir)
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
