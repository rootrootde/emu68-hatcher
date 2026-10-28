"""GUI background worker threads"""

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from emu68hatcher.builder.state import BuildState
from emu68hatcher.builder.workflow import BuildWorkflow
from emu68hatcher.config.schema import BuildConfig

logger = logging.getLogger(__name__)


class BuildWorker(QThread):
    """run a BuildWorkflow on a Qt thread"""

    progress_updated = Signal(str, float, str)  # stage, progress, message
    log_event = Signal(str, str)  # stage, message
    build_finished = Signal(bool, str, str)  # success, output_path, error
    unverified_question = Signal(list)  # "name: reason" per failed optional package

    def __init__(self, config: BuildConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._lock = threading.Lock()
        self._cancelled = False
        self._workflow: BuildWorkflow | None = None
        self._answered = threading.Event()
        self._accept_unverified = False

    def run(self):
        """drive the workflow, forward callbacks as Qt signals"""
        # uncaught exceptions die silently in a QThread; without this guard the
        # dialog never receives build_finished and freezes at "Initializing"
        try:
            self._run()
        except Exception as e:
            self.build_finished.emit(False, "", f"{type(e).__name__}: {e}")

    def _run(self):
        def progress_callback(state: BuildState):
            self.progress_updated.emit(
                state.stage.value,
                state.progress,
                state.message,
            )

        def log_callback(stage: str, message: str):
            self.log_event.emit(stage, message)

        workflow = BuildWorkflow(
            self.config,
            progress_callback=progress_callback,
            log_callback=log_callback,
            confirm_unverified=self._confirm_unverified,
        )

        with self._lock:
            self._workflow = workflow
            already_cancelled = self._cancelled
        if already_cancelled:
            workflow.cancel()

        result = workflow.build()

        if result.success:
            self.build_finished.emit(
                True,
                str(result.output_path) if result.output_path else "",
                "",
            )
        else:
            self.build_finished.emit(
                False,
                "",
                result.error or "Unknown error",
            )

    def _confirm_unverified(self, lines: list[str]) -> bool:
        """ask the dialog on the gui thread and wait; a cancel counts as no"""
        self._answered.clear()
        self.unverified_question.emit(lines)
        while not self._answered.wait(0.25):
            with self._lock:
                if self._cancelled:
                    return False
        return self._accept_unverified

    def answer_unverified(self, accept: bool) -> None:
        self._accept_unverified = accept
        self._answered.set()

    def cancel(self):
        """thread-safe cancel request"""
        with self._lock:
            self._cancelled = True
            workflow = self._workflow
        if workflow is not None:
            workflow.cancel()


class ToolDownloadWorker(QThread):
    """fetch missing host tools"""

    tool_started = Signal(str)  # tool_name
    tool_progress = Signal(str, int, int)  # tool_name, bytes_downloaded, bytes_total
    tool_finished = Signal(str, bool)  # tool_name, success
    download_finished = Signal(bool, list)  # overall success, failed tool names

    def run(self):
        """download each missing or stale tool, emit per-tool progress"""
        try:
            self._run()
        except Exception:
            logger.exception("tool download failed")
            self.download_finished.emit(False, ["hst-imager", "7z"])

    def _run(self):
        from emu68hatcher.builder.host.tools import (
            download_7zip,
            download_tool,
            tool_needs_download,
        )

        pending = [t for t in ("hst-imager", "7z") if tool_needs_download(t)]

        if not pending:
            self.download_finished.emit(True, [])
            return

        failed: list[str] = []
        for index, tool_name in enumerate(pending):
            if self.isInterruptionRequested():
                failed.extend(pending[index:])
                break
            self.tool_started.emit(tool_name)

            # one signal per percent, not per 8 KiB chunk: pyside6 6.12.0 lost a reference per emit
            sent = [-1]

            def _cb(downloaded: int, total: int, _name=tool_name, _sent=sent):
                step = downloaded * 100 // total if total else downloaded >> 20
                if step != _sent[0]:
                    _sent[0] = step
                    self.tool_progress.emit(_name, downloaded, total)

            try:
                if tool_name == "7z":
                    result = download_7zip(progress_callback=_cb)
                else:
                    result = download_tool(tool_name, force=True, progress_callback=_cb)
            except Exception:
                logger.exception(f"Error downloading {tool_name}")
                result = None

            success = result is not None
            if not success:
                failed.append(tool_name)
            self.tool_finished.emit(tool_name, success)

        self.download_finished.emit(len(failed) == 0, failed)


class UpdateCheckWorker(QThread):
    """Fetch the signed update manifest."""

    check_finished = Signal(object)

    def run(self):
        from emu68hatcher.data.manifest_state import (
            ManifestSelection,
            check_remote_manifest,
            get_active_selection,
        )

        try:
            result = check_remote_manifest()
        except Exception as error:
            logger.exception("update check failed")
            try:
                active = get_active_selection()
            except Exception:
                logger.exception("no active manifest to report the failed update check against")
                self.check_finished.emit(None)
                return
            result = ManifestSelection(
                active.manifest,
                active.source,
                error=str(error) or type(error).__name__,
                checked=True,
            )
        self.check_finished.emit(result)


class ApplicationUpdateDownloadWorker(QThread):
    """Download and verify one application installer."""

    download_progress = Signal(int, int)
    download_finished = Signal(bool, str)

    def __init__(self, artifact, destination_dir: Path, parent=None):
        super().__init__(parent)
        self.artifact = artifact
        self.destination_dir = destination_dir

    def run(self):
        from emu68hatcher.data.app_update import download_hatcher_artifact

        try:
            path = download_hatcher_artifact(
                self.artifact,
                self.destination_dir,
                progress=lambda current, total: self.download_progress.emit(current, total),
                cancelled=self.isInterruptionRequested,
            )
        except Exception as error:
            if not self.isInterruptionRequested():
                logger.exception("application update download failed")
            self.download_finished.emit(False, str(error) or type(error).__name__)
            return
        self.download_finished.emit(True, str(path))


class ROMScanWorker(QThread):
    """scan one or more directories for Kickstart ROMs"""

    scan_finished = Signal(list, bool)  # (found ROMs, truncated)
    scan_error = Signal(str)

    def __init__(self, directories: Path | list[Path], parent=None):
        super().__init__(parent)
        self.directories = [directories] if isinstance(directories, Path) else list(directories)

    def run(self):
        """scan + emit"""
        from emu68hatcher.data.rom_detection import scan_for_kickstart_roms

        try:
            found_roms, truncated = scan_for_kickstart_roms(
                self.directories,
                cancel_check=self.isInterruptionRequested,
            )
        except Exception as e:
            logger.exception("ROM scan failed")
            self.scan_error.emit(str(e) or type(e).__name__)
            return
        if self.isInterruptionRequested():
            return
        self.scan_finished.emit(found_roms, truncated)


class ADFScanWorker(QThread):
    """scan one or more directories for Workbench ADFs"""

    scan_finished = Signal(list, bool)  # (found media, truncated)
    scan_error = Signal(str)

    def __init__(self, directories: Path | list[Path], parent=None):
        super().__init__(parent)
        self.directories = [directories] if isinstance(directories, Path) else list(directories)

    def run(self):
        """scan + emit"""
        from emu68hatcher.data.install_media import scan_install_media_by_hash

        try:
            found_media, truncated = scan_install_media_by_hash(
                self.directories,
                cancel_check=self.isInterruptionRequested,
            )
        except Exception as e:
            logger.exception("install media scan failed")
            self.scan_error.emit(str(e) or type(e).__name__)
            return
        if self.isInterruptionRequested():
            return
        self.scan_finished.emit(found_media, truncated)


class ExtraContentSizeWorker(QThread):
    """measure extra content directories"""

    size_found = Signal(str, object)
    scan_error = Signal(str, str)

    def __init__(self, directories: list[Path], parent=None):
        super().__init__(parent)
        self.directories = directories

    def run(self):
        from emu68hatcher.builder.staging.tree_copy import measure_contained_tree

        for directory in self.directories:
            if self.isInterruptionRequested():
                return
            try:
                usage = measure_contained_tree(
                    directory,
                    cancel_check=self.isInterruptionRequested,
                )
            except InterruptedError:
                return
            except Exception as e:
                logger.exception("extra content size check failed")
                self.scan_error.emit(str(directory), str(e) or type(e).__name__)
                continue
            if self.isInterruptionRequested():
                return
            self.size_found.emit(str(directory), usage)


class DiskListWorker(QThread):
    """enumerate removable disks off the GUI thread"""

    disks_loaded = Signal(list)  # list[DiskInfo]
    load_error = Signal(str)

    def run(self):
        from emu68hatcher.builder.host.disk_enum import list_removable_disks

        try:
            disks = list_removable_disks(raise_on_error=True)
        except Exception as e:
            logger.exception("removable disk enumeration failed")
            self.load_error.emit(str(e) or type(e).__name__)
            return
        if self.isInterruptionRequested():
            return
        self.disks_loaded.emit(disks)
