"""Tools and update controls shown directly on Overview."""

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.gui.tool_status_panel import ToolStatusPanel
from emu68hatcher.gui.update_panel import UpdatePanel


class StartTab(QWidget):
    """Tool downloads and update status, without a separate welcome page."""

    catalog_changed = Signal()
    status_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tool_panel = ToolStatusPanel(self)
        self.update_panel = UpdatePanel(self)
        self.tool_panel.status_changed.connect(self.status_changed)
        self.update_panel.status_changed.connect(self.status_changed)
        self.update_panel.catalog_changed.connect(self.catalog_changed)
        self._setup_ui()
        self.refresh_status()
        self.update_panel.refresh_update_status()

    # --- UI ---
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        self.updates_group = QGroupBox("Updates and tools")
        tools_layout = QVBoxLayout(self.updates_group)
        tools_layout.setSpacing(12)

        self.tool_panel.add_tool_rows(tools_layout)

        # buttons
        btn_row = QHBoxLayout()

        self.reset_btn = QPushButton("Reset App Data…")
        self.reset_btn.clicked.connect(self.reset_app_data)
        btn_row.addWidget(self.reset_btn)

        btn_row.addStretch()

        self.tool_panel.add_buttons(btn_row)

        tools_layout.addLayout(btn_row)
        layout.addWidget(self.updates_group)

        self.update_panel.add_rows(tools_layout)

        # download progress group (hidden until a download starts)
        self.tool_panel.add_progress(layout)

    @Slot()
    def refresh_status(self):
        self.tool_panel.refresh_status()

    @Slot()
    def check_for_updates(self):
        self.update_panel.check_for_updates()

    def set_catalog_build_active(self, active: bool):
        self.update_panel.set_catalog_build_active(active)

    # --- reset ---
    @Slot()
    def reset_app_data(self):
        """wipe cache, temp files and downloaded tools after confirmation"""
        if any(
            worker is not None and worker.isRunning()
            for worker in self.tool_panel.workers() + self.update_panel.workers()
        ):
            return

        box = QMessageBox(self)
        box.setWindowTitle("Reset App Data")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Delete all cached downloads, temporary files and downloaded tools?")
        box.setInformativeText(
            "This resets the app to its fresh-install state. Tools and packages are "
            "re-downloaded when needed. Saved build configs are not touched."
        )
        delete_btn = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not delete_btn:
            return

        from emu68hatcher.data.package_loader import clear_package_caches
        from emu68hatcher.utils.paths import reset_runtime_data

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            failures = reset_runtime_data()
        finally:
            QApplication.restoreOverrideCursor()

        self.tool_panel.progress_group.setVisible(False)
        self.update_panel.reset_selection()
        clear_package_caches()
        self.catalog_changed.emit()
        self.refresh_status()
        self.update_panel.refresh_update_status()
        if failures:
            QMessageBox.warning(
                self,
                "Reset App Data",
                "Could not remove:\n" + "\n".join(str(p) for p in failures),
            )

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        tools_done = self.tool_panel.shutdown_workers(timeout_ms)
        updates_done = self.update_panel.shutdown_workers(timeout_ms)
        return tools_done and updates_done
