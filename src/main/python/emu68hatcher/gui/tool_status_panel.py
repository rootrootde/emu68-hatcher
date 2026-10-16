"""Host tool rows and the tool download flow on Overview."""

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.gui.design import set_status_icon

_TOOL_ROWS = [
    ("hst-imager", "Disk image creation and manipulation"),
    ("7z", "Archive extraction (p7zip)"),
]


class ToolStatusPanel(QObject):
    """Tool install state, tool downloads and the download progress box for the host widget."""

    status_changed = Signal()

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._host = host
        self._worker = None
        self._row_widgets: dict[str, tuple[QLabel, QLabel]] = {}
        self._fresh_downloads: set[str] = set()

    def add_tool_rows(self, tools_layout: QVBoxLayout) -> None:
        from emu68hatcher.builder.host.tools import TOOL_LABELS

        for name, description in _TOOL_ROWS:
            row = QHBoxLayout()
            row.setSpacing(10)

            status_label = QLabel()
            set_status_icon(
                status_label,
                "pending",
                "Checking",
            )

            text_col = QVBoxLayout()
            text_col.setSpacing(2)

            name_label = QLabel(f"<b>{TOOL_LABELS[name]}</b>")
            name_label.setToolTip(description)
            name_label.setTextFormat(Qt.TextFormat.RichText)
            name_label.setWordWrap(True)

            path_label = QLabel("")
            path_label.setProperty("tone", "muted")
            path_label.setWordWrap(True)
            path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

            text_col.addWidget(name_label)
            text_col.addWidget(path_label)

            row.addWidget(status_label)
            row.addLayout(text_col, 1)
            tools_layout.addLayout(row)

            self._row_widgets[name] = (status_label, path_label)

    def add_buttons(self, btn_row: QHBoxLayout) -> None:
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_status)
        btn_row.addWidget(self.refresh_btn)

        self.download_btn = QPushButton("Download Missing Tools…")
        self.download_btn.setProperty("primary", True)
        self.download_btn.clicked.connect(self.start_download)
        btn_row.addWidget(self.download_btn)

    def add_progress(self, layout: QVBoxLayout) -> None:
        self.progress_group = QGroupBox("Download Progress")
        progress_layout = QVBoxLayout(self.progress_group)

        self.progress_status = QLabel("")
        progress_layout.addWidget(self.progress_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        layout.addWidget(self.progress_group)
        self.progress_group.setVisible(False)

    def workers(self) -> list:
        return [self._worker]

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        running = [worker for worker in self.workers() if worker is not None and worker.isRunning()]
        for worker in running:
            worker.requestInterruption()
        for worker in running:
            worker.wait(timeout_ms)
        return not any(worker.isRunning() for worker in running)

    @Slot()
    def refresh_status(self):
        """re-query tool install state and repaint the rows"""
        from emu68hatcher.builder.host.tools import tool_needs_download
        from emu68hatcher.utils.host_tools import find_7z, find_hst_imager

        finders = {
            "hst-imager": find_hst_imager,
            "7z": find_7z,
        }
        any_missing = False
        any_stale = False

        for name, _ in _TOOL_ROWS:
            path = finders[name]()
            status_label, path_label = self._row_widgets[name]
            if not path:
                set_status_icon(
                    status_label,
                    "error",
                    "Missing",
                )
                path_label.setText("Not installed")
                path_label.setToolTip("")
                any_missing = True
            elif tool_needs_download(name):
                set_status_icon(
                    status_label,
                    "warning",
                    "Update available",
                )
                path_label.setText("Update available")
                path_label.setToolTip(str(path))
                any_stale = True
            else:
                set_status_icon(
                    status_label,
                    "ok",
                    "Installed",
                )
                path_label.setText("Installed")
                path_label.setToolTip(str(path))

        self.download_btn.setEnabled(any_missing or any_stale)
        if any_missing:
            self.download_btn.setText("Download Missing Tools…")
        elif any_stale:
            self.download_btn.setText("Update Tools…")
        else:
            self.download_btn.setText("All Tools Installed")
        self.status_changed.emit()

    @Slot()
    def start_download(self):
        from emu68hatcher.gui.workers import ToolDownloadWorker

        if self._worker and self._worker.isRunning():
            return

        self.download_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.progress_group.setVisible(True)
        self.progress_status.setText("Preparing download…")
        self.progress_bar.setValue(0)

        self._fresh_downloads.clear()
        self._worker = ToolDownloadWorker(self)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.tool_progress.connect(self._on_tool_progress)
        self._worker.tool_finished.connect(self._on_tool_finished)
        self._worker.download_finished.connect(self._on_download_finished)
        self._worker.finished.connect(self._worker_finished)
        self._worker.start()

    @Slot()
    def _worker_finished(self) -> None:
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        worker.deleteLater()

    @Slot(str)
    def _on_tool_started(self, tool_name: str):
        from emu68hatcher.builder.host.tools import TOOL_LABELS

        label = TOOL_LABELS.get(tool_name, tool_name)
        self.progress_status.setText(f"Downloading {label}…")
        self.progress_bar.setValue(0)

    @Slot(str, int, int)
    def _on_tool_progress(self, tool_name: str, downloaded: int, total: int):
        from emu68hatcher.builder.host.tools import TOOL_LABELS

        label = TOOL_LABELS.get(tool_name, tool_name)
        if total > 0:
            pct = int((downloaded / total) * 100)
            self.progress_bar.setValue(min(pct, 100))
            mb_down = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.progress_status.setText(f"Downloading {label} - {mb_down:.1f} / {mb_total:.1f} MB")
        else:
            self.progress_bar.setRange(0, 0)  # indeterminate
            self.progress_status.setText(f"Downloading {label}…")

    @Slot(str, bool)
    def _on_tool_finished(self, tool_name: str, success: bool):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if success else 0)
        if success:
            self._fresh_downloads.add(tool_name)

    @Slot(bool, list)
    def _on_download_finished(self, success: bool, failed: list):
        self.refresh_btn.setEnabled(True)
        self.refresh_status()

        if success:
            self.progress_status.setText("All tools downloaded successfully.")
            self.progress_bar.setValue(100)
        else:
            from emu68hatcher.builder.host.tools import TOOL_LABELS

            failed_list = (
                ", ".join(TOOL_LABELS.get(t, t) for t in failed) if failed else "one or more tools"
            )
            hint = ""
            if "7z" in failed:
                hint = " (macOS: install with <code>brew install p7zip</code>)"
            self.progress_status.setText(f"Failed to download: {failed_list}.{hint}")
            self.progress_status.setTextFormat(Qt.TextFormat.RichText)

        from emu68hatcher.utils.platform import OperatingSystem, get_platform_info

        if (
            get_platform_info().os == OperatingSystem.MACOS
            and "hst-imager" in self._fresh_downloads
        ):
            self._offer_macos_tcc_registration()

    def _offer_macos_tcc_registration(self):
        """ask whether to file hst-imager with tccd now; deferring just means the first build does it"""
        from emu68hatcher.builder.host.macos_tcc import (
            open_full_disk_access_pane,
            register_hst_imager_with_tcc,
        )
        from emu68hatcher.utils.paths import get_tools_dir

        hst = get_tools_dir() / "hst-imager"
        if not hst.is_file():
            return

        box = QMessageBox(self._host)
        box.setWindowTitle("Enable disk access for hst-imager")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("hst-imager needs Full Disk Access to write SD cards.")
        box.setInformativeText(
            "You'll get a password prompt, then System Settings opens - enable "
            "<b>hst-imager</b> there. You can skip and do it on the first build."
        )
        setup_btn = box.addButton("Set Up Now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not setup_btn:
            return

        if register_hst_imager_with_tcc(hst):
            open_full_disk_access_pane()
