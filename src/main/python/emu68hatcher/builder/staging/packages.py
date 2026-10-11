"""package installer driven by YAML defs - extract (incl nested), copy locals"""

import shutil
import struct
import tempfile
from pathlib import Path

from emu68hatcher.builder.host.archive import ARCHIVE_EXTENSIONS, extract_archive
from emu68hatcher.builder.staging.files import (
    ci_match_child,
    require_contained_path,
    resolve_source_path,
    resolve_staging_path,
)
from emu68hatcher.builder.staging.tree_copy import copy_contained_tree
from emu68hatcher.config.defaults import DEFAULT_BOOT_DEVICE
from emu68hatcher.data.package_loader import get_package_by_name
from emu68hatcher.data.package_schema import InstallRule, Package
from emu68hatcher.utils.logging import get_logger
from emu68hatcher.utils.paths import ensure_dir


def _ci_glob_pattern(pattern: str) -> str:
    """expand a glob with case-insensitive character classes"""
    result = []
    for ch in pattern:
        if ch.isalpha():
            result.append(f"[{ch.lower()}{ch.upper()}]")
        else:
            result.append(ch)
    return "".join(result)


def _set_icon_stack(info_path: Path, size: int) -> None:
    """patch a workbench icon's do_StackSize (BE long at offset 74) so WB launches it with more stack"""
    data = bytearray(info_path.read_bytes())
    # only touch real DiskObjects (magic 0xE310); leave anything else untouched
    if len(data) >= 78 and data[0] == 0xE3 and data[1] == 0x10:
        struct.pack_into(">i", data, 74, size)
        info_path.write_bytes(data)


def _merge_tree(source: Path, dest: Path) -> int:
    """recursively merge source into dest; same-name collisions overwrite (case-insensitive)"""
    result = copy_contained_tree(source, dest, resolve_target=resolve_staging_path)
    if result.skipped_cycles:
        get_logger().warning(
            f"Skipped {result.skipped_cycles} repeated directories while copying {source}"
        )
    return result.files_copied


class PackageInstaller:
    """installs packages per YAML defs - extract, copy locals"""

    def __init__(
        self,
        staging_dir: Path,
        extracted_packages_dir: Path,
        local_packages_dir: Path | None = None,
        boot_device: str | None = None,
        extracted_paths: dict[str, Path] | None = None,
    ):
        # must match the tree configure/install_workbench stage into, or finalize splits the device
        self.boot_device = boot_device or DEFAULT_BOOT_DEVICE
        self.staging_dir = staging_dir
        self.extracted_dir = extracted_packages_dir
        self.local_packages_dir = local_packages_dir
        # the extraction stage can link package roots into the download cache.
        self._extracted_roots = frozenset(
            path.resolve() for path in (extracted_paths or {}).values()
        )
        self.logger = get_logger()

    def install_package(self, package_name: str) -> int:
        """install a single package"""
        pkg = get_package_by_name(package_name)

        if not pkg:
            self.logger.warning(f"Package not found: {package_name}")
            return 0

        files_installed = 0

        source_dir = self._get_source_dir(pkg)

        for rule in pkg.install:
            # Cached catalogs can still contain the former font documentation rules.
            destination = rule.dest.strip("/").casefold()
            if destination == "emu68-hatcher/fonts" or destination.startswith(
                "emu68-hatcher/fonts/"
            ):
                continue
            count = self._apply_install_rule(rule, source_dir)
            files_installed += count

        return files_installed

    def has_package_source(self, package_name: str) -> bool:
        """Return whether a package with install rules has a usable source tree."""
        pkg = get_package_by_name(package_name)
        if not pkg or not (pkg.download or pkg.archive_package) or not pkg.install:
            return True
        return self._get_source_dir(pkg) is not None

    def _get_source_dir(self, pkg: Package) -> Path | None:
        """get the source directory for package files"""
        if pkg.archive_package:
            source = get_package_by_name(pkg.archive_package)
            return self._get_source_dir(source) if source else None
        if not pkg.download:
            return None

        if pkg.download.source.value == "local":
            # extracted dir first (for local archives), fall back to local_packages_dir
            source_dir = self.extracted_dir / pkg.name
            if source_dir.exists():
                return source_dir
            return self.local_packages_dir

        source_dir = self.extracted_dir / pkg.name
        if source_dir.exists():
            return source_dir

        # try filename without archive extension
        if pkg.download.filename:
            base_name = pkg.download.filename
            for ext in [".lha", ".zip", ".7z", ".tar.gz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[: -len(ext)]
                    break

            source_dir = self.extracted_dir / base_name
            if source_dir.exists():
                return source_dir

        # try case-insensitive search
        matched = ci_match_child(self.extracted_dir, pkg.name)
        if matched and (self.extracted_dir / matched).is_dir():
            return self.extracted_dir / matched

        self.logger.debug(f"Source directory not found for {pkg.name}")
        return None

    def _resolve_nested_archive(self, source_dir: Path, path_pattern: str) -> tuple[Path, str]:
        """resolve path with nested archives - e.g. "Contrib/Emu68Info.lha/Emu68Info" extracts the .lha and returns (new base, remainder)"""
        parts = path_pattern.split("/")
        current_path = source_dir

        for i, part in enumerate(parts):
            matched = ci_match_child(current_path, part)
            next_path = require_contained_path(source_dir, current_path / (matched or part))

            if next_path.is_file():
                suffix = next_path.suffix.lower()
                if suffix in ARCHIVE_EXTENSIONS:
                    extract_dir = require_contained_path(
                        source_dir, next_path.parent / f"_extracted_{next_path.stem}"
                    )

                    if not extract_dir.exists():
                        self.logger.info(f"Extracting nested archive: {next_path.name}")
                        temp_dir = Path(
                            tempfile.mkdtemp(
                                prefix=f".{extract_dir.name}-",
                                dir=extract_dir.parent,
                            )
                        )
                        result = extract_archive(next_path, temp_dir)

                        if not result.success:
                            shutil.rmtree(temp_dir)
                            self.logger.warning(
                                f"Failed to extract nested archive {next_path}: {result.error}"
                            )
                            return source_dir, path_pattern
                        temp_dir.replace(extract_dir)

                    remaining = "/".join(parts[i + 1 :])
                    return extract_dir, remaining

            elif next_path.exists():
                current_path = next_path
            else:
                break

        return source_dir, path_pattern

    def _apply_install_rule(
        self,
        rule: InstallRule,
        source_dir: Path | None,
    ) -> int:
        if not source_dir:
            return 0
        allowed = [self.extracted_dir] + (
            [self.local_packages_dir] if self.local_packages_dir else []
        )
        resolved_source = source_dir.resolve()
        if resolved_source not in self._extracted_roots and not any(
            resolved_source.is_relative_to(root.resolve()) for root in allowed
        ):
            raise ValueError(f"package source leaves extraction directory: {source_dir}")
        source_dir, source_pattern = self._resolve_nested_archive(source_dir, rule.source)
        dest_base = self.staging_dir / self.boot_device
        dest_dir = resolve_staging_path(dest_base, rule.dest.strip("/"))
        ensure_dir(dest_dir)
        if "*" in source_pattern:
            return self._install_wildcard(rule, source_dir, source_pattern, dest_dir)
        return self._install_exact(rule, source_dir, source_pattern, dest_dir)

    def _install_wildcard(
        self,
        rule: InstallRule,
        source_dir: Path,
        source_pattern: str,
        dest_dir: Path,
    ) -> int:
        parts = source_pattern.split("/")
        first_glob = next(index for index, part in enumerate(parts) if "*" in part)
        base_parts = parts[:first_glob]
        glob_part = "/".join(parts[first_glob:])
        search_dir = source_dir
        if base_parts:
            resolved = resolve_source_path(source_dir, "/".join(base_parts))
            if resolved:
                search_dir = resolved

        strip_levels = len(glob_part.split("/")) - 1
        installed = 0
        if not search_dir.exists():
            return installed
        for source_item in search_dir.glob(_ci_glob_pattern(glob_part)):
            require_contained_path(source_dir, source_item)
            relative = source_item.relative_to(search_dir).parts
            keep = relative[strip_levels:] if len(relative) > strip_levels else (source_item.name,)
            destination = resolve_staging_path(
                dest_dir,
                rule.rename or str(Path(*keep)),
            )
            installed += self._copy_item(
                source_item,
                destination,
                rule.stack,
                rule.xor_byte,
                merge_dirs=True,
            )
        return installed

    def _install_exact(
        self,
        rule: InstallRule,
        source_dir: Path,
        source_pattern: str,
        dest_dir: Path,
    ) -> int:
        source = resolve_source_path(source_dir, source_pattern)
        if source is None:
            source = source_dir / source_pattern
        if not source.exists():
            return 0
        destination = resolve_staging_path(dest_dir, rule.rename or source.name)
        return self._copy_item(
            source,
            destination,
            rule.stack,
            rule.xor_byte,
            merge_dirs=rule.recursive,
        )

    @staticmethod
    def _copy_item(
        source: Path,
        destination: Path,
        stack: int | None,
        xor_byte: int | None,
        merge_dirs: bool,
    ) -> int:
        if not source.is_file() and not source.is_dir():
            return 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir() and merge_dirs:
            return _merge_tree(source, destination)
        shutil.copy2(source, destination)
        if xor_byte is not None:
            destination.write_bytes(bytes(value ^ xor_byte for value in destination.read_bytes()))
        if stack:
            _set_icon_stack(destination, stack)
        return 1
