"""Eleven-stage build workflow from validation through optional flashing."""

import logging
import platform as _platform
import subprocess
import sys
from pathlib import Path

from emu68hatcher.builder.errors import BuildCancelledError, BuildError
from emu68hatcher.builder.host.elevation import cleanup_elevation
from emu68hatcher.builder.state import (
    BuildLogCallback,
    BuildProgressCallback,
    BuildResult,
    BuildStage,
    BuildState,
    CreatedImage,
)
from emu68hatcher.config.schema import BuildConfig
from emu68hatcher.data.catalog import CatalogSnapshot, get_catalog_snapshot, use_catalog
from emu68hatcher.data.package_resolver import Resolution
from emu68hatcher.utils.logging import get_logger


class BuildLogHandler(logging.Handler):
    """INFO+ records on the emu68hatcher logger -> workflow.log_callback"""

    def __init__(self, workflow: "BuildWorkflow"):
        super().__init__(level=logging.INFO)
        self.workflow = workflow

    def emit(self, record: logging.LogRecord) -> None:
        cb = self.workflow._log_callback
        if cb is None:
            return
        try:
            stage = self.workflow.state.stage.value
            cb(stage, record.getMessage())
        except Exception:
            self.handleError(record)


class BuildWorkflow:
    """run pipeline stages in sequence, update BuildState, fire progress callbacks"""

    def __init__(
        self,
        config: BuildConfig,
        progress_callback: BuildProgressCallback | None = None,
        log_callback: BuildLogCallback | None = None,
        catalog: CatalogSnapshot | None = None,
    ):
        self.catalog = catalog or get_catalog_snapshot()
        self.config = config
        self.progress_callback = progress_callback
        self._log_callback = log_callback
        self.state = BuildState()
        self._resolution: Resolution | None = None
        self.logger = get_logger()
        self._cancelled = False

    def cancel(self) -> None:
        """request build cancellation"""
        self._cancelled = True

    def _update_state(
        self,
        stage: BuildStage | None = None,
        progress: float | None = None,
        message: str | None = None,
    ) -> None:
        """patch state, fire progress callback"""
        if stage is not None:
            self.state.stage = stage
        if progress is not None:
            self.state.progress = progress
        if message is not None:
            self.state.message = message

        if self.progress_callback:
            self.progress_callback(self.state)

    def _log(self, message: str) -> None:
        """status label only - use _milestone() for console/buildlog/gui-log"""
        self._update_state(message=message)

    def _milestone(self, message: str) -> None:
        """status label + INFO log (console + gui log + buildlog)"""
        self._update_state(message=message)
        self.logger.info(message)

    def _buildlog_path(self) -> Path:
        """buildlog path - alongside the output image if set, else cache_dir"""
        from emu68hatcher.config.schema import OutputType
        from emu68hatcher.utils.paths import get_cache_dir

        # DEVICE output: path is \\.\PhysicalDriveN / /dev/diskN - statting it opens the raw
        # device (untimed CreateFileW, can block or raise on a wedged stack); never probe it
        if (
            self.config.output
            and self.config.output.path
            and self.config.output.type != OutputType.DEVICE
        ):
            out = Path(self.config.output.path)
            parent = out.parent if out.suffix else out
            if parent.exists() and parent.is_dir():
                return parent / "buildlog.txt"
        return get_cache_dir() / "buildlog.txt"

    def _attach_build_log(self):
        """per-build file handler; overwrites previous log"""
        from emu68hatcher.utils.logging import attach_file_handler

        path = self._buildlog_path()
        # log the chosen path BEFORE attach so the GUI log records it even if open fails
        self.logger.info(f"buildlog target: {path}")
        handler = attach_file_handler(
            self.logger.logger,
            path,
            mode="w",
            fmt="%(asctime)s %(levelname)-7s %(message)s",
            datefmt="%H:%M:%S",
        )
        if handler is None:
            self.logger.warning(f"Could not open buildlog at {path}")
            return None, None
        # marker + flush so the file is non-empty if the build crashes early
        self.logger.info(f"buildlog opened: {path}")
        try:
            handler.flush()
        except Exception:
            pass
        return handler, path

    def _check_cancelled(self) -> None:
        """raise BuildCancelledError if cancelled"""
        if self._cancelled:
            raise BuildCancelledError("Build was cancelled by user")

    def _bring_target_disk_online(self) -> None:
        """windows: undo the Set-Disk -IsOffline applied at unmount"""
        from emu68hatcher.builder.host.disk_enum import find_disk, online_disk
        from emu68hatcher.config.schema import OutputType

        if self.config.output is None:
            return
        targets: list[str] = []
        if self.config.output.type == OutputType.DEVICE:
            targets.append(str(self.config.output.path))
        if self.config.output.flash_target:
            targets.append(str(self.config.output.flash_target))
        for device in targets:
            info = find_disk(device)
            if info is None:
                continue
            try:
                result = online_disk(info, self.logger, elevation=self.state.elevation)
                if not result.success:
                    self.logger.warning(f"could not bring {device} online: {result.error}")
            except Exception:
                self.logger.exception(f"error bringing {device} online")

    def _log_platform_info(self) -> None:
        """dump build env + tool paths to buildlog.txt; helps cross-platform debugging"""
        from emu68hatcher.utils.host_tools import find_hst_imager, get_hst_imager_env
        from emu68hatcher.utils.paths import get_tools_dir

        log = self.logger.info
        log("platform: === build environment ===")
        log(f"platform: os: {_platform.system()} {_platform.release()} ({_platform.version()})")
        log(f"platform: python: {sys.version.split()[0]} ({sys.platform})")
        log(f"platform: tools dir: {get_tools_dir()}")

        hst = find_hst_imager()
        log(f"platform: hst-imager binary: {hst}")
        if hst:
            try:
                ver = subprocess.run(
                    [str(hst), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=get_hst_imager_env(),
                )
                first_line = (ver.stdout or ver.stderr or "").strip().splitlines()
                log(
                    f"platform: hst-imager --version: "
                    f"{first_line[0] if first_line else '(empty)'} "
                    f"(rc={ver.returncode})"
                )
            except (subprocess.SubprocessError, OSError) as e:
                log(f"platform: hst-imager --version: unavailable ({e})")

        if self.config.output:
            out_path = self.config.output.path
            posix = f" posix={out_path.as_posix()!s}" if isinstance(out_path, Path) else ""
            log(f"platform: output: type={self.config.output.type.value} raw={out_path!s}{posix}")
        else:
            log("platform: output: not configured")
        log("platform: === end build environment ===")

    def _finalize_output_move(self, image: CreatedImage) -> CreatedImage:
        """move a .img built in the work dir (macOS TCC case) to the user's chosen output path"""
        final = image.final_output_path
        src = image.image_path
        if final is None or src is None or src == final:
            return image
        import shutil

        self._milestone(f"Moving image to {final}")
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            final.unlink()
        shutil.move(str(src), str(final))
        self.logger.info(f"Moved built image to {final}")
        from dataclasses import replace

        return replace(image, image_path=final, final_output_path=None)

    def build(self) -> BuildResult:
        with use_catalog(self.catalog):
            return self._build_with_catalog()

    def _build_with_catalog(self) -> BuildResult:
        """run the full pipeline synchronously"""
        from emu68hatcher.builder.stage_registry import PIPELINE_STAGES

        # GUI handler first: the buildlog probe below touches the output location, and its
        # breadcrumbs must reach the dialog if that goes wrong (console is invisible when frozen)
        gui_log_handler = BuildLogHandler(self)
        self.logger.logger.addHandler(gui_log_handler)
        buildlog_handler = None
        buildlog_path = None

        try:
            buildlog_handler, buildlog_path = self._attach_build_log()
            if buildlog_path:
                self.logger.info(f"Build log: {buildlog_path}")
            self.logger.info(
                f"catalog: {self.catalog.id}, revision {self.catalog.revision}, "
                f"source {self.catalog.source_commit}"
            )
            self._log_platform_info()

            phase = None
            for definition in PIPELINE_STAGES:
                phase = definition.function(self, phase)
                self._check_cancelled()

            assert isinstance(phase, CreatedImage)
            phase = self._finalize_output_move(phase)
            self._update_state(BuildStage.COMPLETE, 100.0)
            self._milestone("Build successful!")
            if buildlog_path:
                self._milestone(f"Build log saved to: {buildlog_path}")

            return BuildResult(
                success=True,
                output_path=self.config.output.path if self.config.output else None,
            )

        except BuildCancelledError:
            self._update_state(BuildStage.FAILED)
            self._milestone("Build cancelled")
            return BuildResult(
                success=False,
                error="Build cancelled by user",
            )

        except BuildError as e:
            self._update_state(BuildStage.FAILED)
            self.logger.error(str(e))
            self._update_state(message=str(e))
            return BuildResult(
                success=False,
                error=str(e),
            )

        except Exception as e:
            self._update_state(BuildStage.FAILED)
            # exception() puts the traceback in the buildlog
            self.logger.exception(f"Unexpected error: {e}")
            self._update_state(message=f"Unexpected error: {e}")
            return BuildResult(
                success=False,
                error=str(e),
            )

        finally:
            if self.state.disk_claim is not None:
                try:
                    self.state.disk_claim.release()
                except Exception:
                    self.logger.exception("error releasing disk claim")
                self.state.disk_claim = None
            self._bring_target_disk_online()
            cleanup_elevation(self.state.elevation)
            self.state.elevation = None
            self.logger.logger.removeHandler(gui_log_handler)
            if buildlog_handler is not None:
                self.logger.logger.removeHandler(buildlog_handler)
                buildlog_handler.close()
