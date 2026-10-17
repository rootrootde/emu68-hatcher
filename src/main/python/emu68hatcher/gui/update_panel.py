"""Hatcher and package-list update rows on Overview."""

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.gui.design import set_status_icon


def _installer_action(path: Path) -> tuple[str, bool] | None:
    from emu68hatcher.utils.platform import (
        OperatingSystem,
        detect_os,
        linux_supports_deb_packages,
    )

    os_name = detect_os()
    suffix = path.suffix.lower()
    if os_name == OperatingSystem.MACOS and suffix == ".dmg":
        return "Open DMG", False
    if os_name == OperatingSystem.WINDOWS and suffix == ".exe":
        return "Run Installer and Quit", True
    if os_name == OperatingSystem.LINUX and suffix == ".deb" and linux_supports_deb_packages():
        return "Open Package Installer", False
    return None


def _format_manifest_revision(revision: int) -> str:
    try:
        revision_date = datetime.fromtimestamp(revision, timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return f"revision {revision}"
    if revision_date.year < 2000:
        return f"revision {revision}"
    return f"{revision_date.isoformat()} (revision {revision})"


class UpdatePanel(QObject):
    """Update check, package-list activation and installer download for the host widget."""

    catalog_changed = Signal()
    status_changed = Signal()

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._catalog_build_active = False
        self._pending_catalog_selection = None
        self._update_worker = None
        self._app_download_worker = None
        from emu68hatcher.data.manifest_state import get_active_selection

        self._update_selection = get_active_selection()

    def add_rows(self, tools_layout: QVBoxLayout) -> None:
        """put the two update rows on top of tools_layout and the download status below it"""
        self.hatcher_update_icon = QLabel()
        set_status_icon(
            self.hatcher_update_icon,
            "pending",
            "Checking",
        )
        self.hatcher_update_label = QLabel("")
        self.hatcher_update_label.setWordWrap(True)
        self.hatcher_update_label.setProperty("tone", "muted")
        hatcher_text = QVBoxLayout()
        hatcher_text.setSpacing(2)
        hatcher_text.addWidget(QLabel("<b>Emu68 Hatcher</b>"))
        hatcher_text.addWidget(self.hatcher_update_label)
        hatcher_row = QHBoxLayout()
        hatcher_row.setSpacing(10)
        hatcher_row.addWidget(self.hatcher_update_icon)
        hatcher_row.addLayout(hatcher_text, 1)
        tools_layout.insertLayout(0, hatcher_row)

        self.manifest_update_icon = QLabel()
        set_status_icon(
            self.manifest_update_icon,
            "pending",
            "Checking",
        )
        self.manifest_update_label = QLabel("")
        self.manifest_update_label.setWordWrap(True)
        self.manifest_update_label.setProperty("tone", "muted")
        manifest_text = QVBoxLayout()
        manifest_text.setSpacing(2)
        manifest_text.addWidget(QLabel("<b>Package list</b>"))
        manifest_text.addWidget(self.manifest_update_label)
        manifest_row = QHBoxLayout()
        manifest_row.setSpacing(10)
        manifest_row.addWidget(self.manifest_update_icon)
        manifest_row.addLayout(manifest_text, 1)
        tools_layout.insertLayout(1, manifest_row)

        self.check_updates_btn = QPushButton("Check now")
        self.check_updates_btn.clicked.connect(self.check_for_updates)
        manifest_row.addWidget(self.check_updates_btn)
        self.open_release_btn = QPushButton("Release page")
        self.open_release_btn.clicked.connect(self.open_release_page)
        hatcher_row.addWidget(self.open_release_btn)
        self.download_update_btn = QPushButton("Download Update…")
        self.download_update_btn.clicked.connect(self.download_application_update)
        hatcher_row.addWidget(self.download_update_btn)

        self.update_download_status = QLabel("")
        self.update_download_status.setVisible(False)
        tools_layout.addWidget(self.update_download_status)
        self.update_download_bar = QProgressBar()
        self.update_download_bar.setRange(0, 100)
        self.update_download_bar.setVisible(False)
        tools_layout.addWidget(self.update_download_bar)

    def workers(self) -> list:
        return [self._update_worker, self._app_download_worker]

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        running = [worker for worker in self.workers() if worker is not None and worker.isRunning()]
        for worker in running:
            worker.requestInterruption()
        for worker in running:
            worker.wait(timeout_ms)
        return not any(worker.isRunning() for worker in running)

    def reset_selection(self) -> None:
        from emu68hatcher.data.manifest_state import initialize_manifest

        self._update_selection = initialize_manifest()

    def refresh_update_status(self):
        from emu68hatcher import __version__
        from emu68hatcher.data.app_update import get_current_artifact, is_newer_version
        from emu68hatcher.utils.platform import (
            OperatingSystem,
            detect_os,
            linux_supports_deb_packages,
        )

        selection = self._update_selection
        release = selection.manifest.hatcher
        newer = is_newer_version(__version__, release.version)
        if newer:
            set_status_icon(
                self.hatcher_update_icon,
                "warning",
                "Update available",
            )
            self.hatcher_update_label.setText(
                f"{release.version} available · installed: {__version__}"
            )
        else:
            set_status_icon(
                self.hatcher_update_icon,
                "ok",
                "Current",
            )
            self.hatcher_update_label.setText(f"Up to date · {__version__}")

        source_label = {
            "bundled": "bundled",
            "cache": "cached",
            "remote": "server",
        }[selection.source]
        revision_label = _format_manifest_revision(selection.manifest.revision)
        if selection.error:
            set_status_icon(
                self.manifest_update_icon,
                "warning",
                "Check failed",
            )
            self.manifest_update_label.setText(
                f"Package list check failed; using {source_label} list: {revision_label}"
            )
            self.manifest_update_label.setToolTip(selection.error)
        elif selection.catalog_notice:
            self.manifest_update_label.setText(selection.catalog_notice)
            self.manifest_update_label.setToolTip("")
            set_status_icon(
                self.manifest_update_icon,
                "info",
                "Previous list active",
            )
        elif selection.source == "remote" and selection.changed:
            set_status_icon(
                self.manifest_update_icon,
                "ok",
                "Updated",
            )
            self.manifest_update_label.setText(
                f"Package list updated from server: {revision_label}"
            )
            self.manifest_update_label.setToolTip("")
        elif selection.checked:
            set_status_icon(
                self.manifest_update_icon,
                "ok",
                "Current",
            )
            self.manifest_update_label.setText(f"Package list is up to date: {revision_label}")
            self.manifest_update_label.setToolTip("")
        else:
            set_status_icon(
                self.manifest_update_icon,
                "ok",
                "Available",
            )
            self.manifest_update_label.setText(
                f"Using {source_label} package list: {revision_label}"
            )
            self.manifest_update_label.setToolTip("")

        artifact = get_current_artifact(selection.manifest)
        self.open_release_btn.setEnabled(newer)
        self.download_update_btn.setEnabled(newer and artifact is not None)
        self.download_update_btn.setText("Download Update…")
        self.download_update_btn.setToolTip("")
        if (
            newer
            and artifact is not None
            and detect_os() == OperatingSystem.LINUX
            and artifact.filename.lower().endswith(".deb")
            and not linux_supports_deb_packages()
        ):
            self.download_update_btn.setText("Download .deb…")
            self.download_update_btn.setToolTip(
                "This package can only be opened directly on Debian-based systems."
            )

    @Slot()
    def check_for_updates(self):
        from emu68hatcher.gui.workers import UpdateCheckWorker

        if self._update_worker and self._update_worker.isRunning():
            return
        self.check_updates_btn.setEnabled(False)
        set_status_icon(
            self.manifest_update_icon,
            "pending",
            "Checking",
        )
        self.manifest_update_label.setText("Checking package list and application version…")
        self._update_worker = UpdateCheckWorker(self)
        self._update_worker.check_finished.connect(self._on_update_check_finished)
        self._update_worker.finished.connect(self._update_worker_finished)
        self._update_worker.start()

    def set_catalog_build_active(self, active: bool):
        self._catalog_build_active = active
        if not active and self._pending_catalog_selection is not None:
            selection = self._pending_catalog_selection
            self._pending_catalog_selection = None
            self._on_update_check_finished(selection)

    @Slot(object)
    def _on_update_check_finished(self, selection):
        if selection is None:
            from dataclasses import replace

            selection = replace(self._update_selection, error="Update check failed", checked=True)
        if self._catalog_build_active and not selection.error:
            from dataclasses import replace

            self._pending_catalog_selection = selection
            self._update_selection = replace(
                selection, catalog_notice="Package list will update after this build."
            )
            self.check_updates_btn.setEnabled(True)
            self.refresh_update_status()
            return
        if not selection.error:
            from emu68hatcher.data.catalog import get_catalog_snapshot
            from emu68hatcher.data.manifest_state import activate_manifest

            try:
                previous = get_catalog_snapshot()
                selection = activate_manifest(selection)
                if selection.catalog != previous:
                    self.catalog_changed.emit()
            except Exception as error:
                from emu68hatcher.data.manifest_state import (
                    ManifestSelection,
                    get_active_selection,
                )

                active = get_active_selection()
                selection = ManifestSelection(
                    active.manifest,
                    active.source,
                    catalog=active.catalog,
                    error=str(error) or type(error).__name__,
                    checked=True,
                )
        self._update_selection = selection
        self.check_updates_btn.setEnabled(True)
        self.refresh_update_status()

    @Slot()
    def _update_worker_finished(self):
        worker = self.sender()
        if worker is self._update_worker:
            self._update_worker = None
        worker.deleteLater()
        self.status_changed.emit()

    @Slot()
    def open_release_page(self):
        url = str(self._update_selection.manifest.hatcher.release_url)
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(
                self._host, "Application Update", "Could not open the download page."
            )

    @Slot()
    def download_application_update(self):
        from emu68hatcher.data.app_update import get_current_artifact
        from emu68hatcher.gui.workers import ApplicationUpdateDownloadWorker

        if self._app_download_worker and self._app_download_worker.isRunning():
            return
        artifact = get_current_artifact(self._update_selection.manifest)
        if artifact is None:
            return
        download_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        destination = Path(download_location) if download_location else Path.home() / "Downloads"
        self.download_update_btn.setEnabled(False)
        self.update_download_status.setText(f"Downloading {artifact.filename}…")
        self.update_download_status.setVisible(True)
        self.update_download_bar.setRange(0, 100)
        self.update_download_bar.setValue(0)
        self.update_download_bar.setVisible(True)
        self._app_download_worker = ApplicationUpdateDownloadWorker(artifact, destination, self)
        self._app_download_worker.download_progress.connect(self._on_update_download_progress)
        self._app_download_worker.download_finished.connect(self._on_update_download_finished)
        self._app_download_worker.finished.connect(self._app_download_worker_finished)
        self._app_download_worker.start()

    @Slot(int, int)
    def _on_update_download_progress(self, current: int, total: int):
        if total > 0:
            self.update_download_bar.setRange(0, 100)
            self.update_download_bar.setValue(min(100, int(current * 100 / total)))
            self.update_download_status.setText(
                f"Downloading update - {current / (1024 * 1024):.1f} / "
                f"{total / (1024 * 1024):.1f} MB"
            )
        else:
            self.update_download_bar.setRange(0, 0)

    @Slot(bool, str)
    def _on_update_download_finished(self, success: bool, detail: str):
        self.update_download_bar.setRange(0, 100)
        self.download_update_btn.setEnabled(True)
        if success:
            self.update_download_bar.setValue(100)
            self.update_download_status.setText(f"Downloaded to {detail}")
            self._show_downloaded_update(Path(detail))
        else:
            self.update_download_bar.setValue(0)
            self.update_download_status.setText("Update download failed")
            QMessageBox.warning(self._host, "Application Update", detail)

    def _show_downloaded_update(self, path: Path) -> None:
        box = QMessageBox(self._host)
        box.setWindowTitle("Application Update")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"{path.name} was downloaded and verified.")

        action = _installer_action(path)
        launch_btn = None
        release_btn = None
        if action is not None:
            label, quit_after_launch = action
            launch_btn = box.addButton(label, QMessageBox.ButtonRole.AcceptRole)
            note = f"Saved to:\n{path}"
            if quit_after_launch:
                note += "\n\nEmu68 Hatcher will close after the installer starts."
            box.setInformativeText(note)
        else:
            box.setInformativeText(
                f"Saved to:\n{path}\n\nThis package cannot be opened automatically on this system."
            )
            release_btn = box.addButton("Open Download Page", QMessageBox.ButtonRole.ActionRole)

        folder_btn = box.addButton("Open Downloads Folder", QMessageBox.ButtonRole.ActionRole)
        later_btn = box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(later_btn)
        box.setEscapeButton(later_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is folder_btn:
            self._open_local_path(path.parent, "Could not open the Downloads folder.")
        elif release_btn is not None and clicked is release_btn:
            self.open_release_page()
        elif launch_btn is not None and clicked is launch_btn:
            if self._open_local_path(path, "Could not open the downloaded installer."):
                if action is not None and action[1]:
                    QTimer.singleShot(0, self._host.window().close)

    def _open_local_path(self, path: Path, error_message: str) -> bool:
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            return True
        QMessageBox.warning(self._host, "Application Update", f"{error_message}\n\n{path}")
        return False

    @Slot()
    def _app_download_worker_finished(self):
        worker = self.sender()
        if worker is self._app_download_worker:
            self._app_download_worker = None
        worker.deleteLater()
