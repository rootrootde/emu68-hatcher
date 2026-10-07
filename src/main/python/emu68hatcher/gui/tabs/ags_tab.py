"""AGS import controls; the shared storage controller owns inspection and layout."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.ags_layout import AGS_VOLUMES
from emu68hatcher.config.ags_models import AGS_ROLES
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.storage_controller import StorageController


class AGSTab(QWidget):
    def __init__(self, parent=None, *, controller=None):
        super().__init__(parent)
        self.controller = controller or StorageController(self)
        self._rendering = False
        layout = page_layout(self)
        layout.setSpacing(10)
        self.enabled_check = QCheckBox("Import AGS")
        self.enabled_check.toggled.connect(self._selection_changed)
        layout.addWidget(self.enabled_check)
        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(10)
        layout.addWidget(self.details)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source image:"))
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Choose an AGS .img or .hdf image")
        self.source_edit.textChanged.connect(self._selection_changed)
        source_row.addWidget(self.source_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        source_row.addWidget(browse)
        self.inspect_btn = QPushButton("Refresh")
        self.inspect_btn.clicked.connect(self.controller.refresh_source)
        source_row.addWidget(self.inspect_btn)
        details_layout.addLayout(source_row)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        details_layout.addWidget(self.result_label)
        self.component_checks = {}
        self.component_sizes = {}
        for role, label in (
            ("whdload", "WHDLoad && AGS (required)"),
            ("games", "Extra games and Premium"),
            ("work", "Work"),
            ("media", "Media"),
        ):
            row = QHBoxLayout()
            check = QCheckBox(label)
            check.setToolTip("Imports the complete source partition at its original size.")
            check.toggled.connect(self._selection_changed)
            self.component_checks[role] = check
            row.addWidget(check)
            row.addStretch()
            size = QLabel("—")
            self.component_sizes[role] = size
            row.addWidget(size)
            details_layout.addLayout(row)
        self.layout_label = QLabel()
        self.layout_label.setWordWrap(True)
        self.layout_label.setTextFormat(Qt.TextFormat.PlainText)
        self.layout_label.setProperty("tone", "muted")
        details_layout.addWidget(self.layout_label)
        self.setToolTip("Imported partitions have fixed sizes. Nothing is written until you build.")
        self.controller.changed.connect(self.render)
        self.render()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select AGS image", "", "Disk images (*.img *.hdf);;All Files (*)"
        )
        if path:
            self.source_edit.setText(path)
            self.enabled_check.setChecked(True)

    def _selection_changed(self):
        if self._rendering:
            return
        enabled = self.enabled_check.isChecked()
        roles = tuple(role for role in AGS_ROLES if self.component_checks[role].isChecked())
        removed = [
            part.volume
            for part in self.controller.model.partitions
            if part.ags_reservation
            and part.extra_content_directory
            and (not enabled or part.ags_reservation.role not in roles)
        ]
        if (
            removed
            and QMessageBox.question(
                self,
                "Remove AGS content",
                "Removing "
                + ", ".join(removed)
                + " also removes their extra-content folder settings. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            self.render()
            return
        self.controller.set_selection(enabled, self.source_edit.text(), roles)

    def render(self):
        state = self.controller
        self._rendering = True
        try:
            self.enabled_check.setChecked(state.enabled)
            self.details.setVisible(state.enabled)
            if self.source_edit.text().strip() != state.source:
                self.source_edit.setText(state.source)
            for role, check in self.component_checks.items():
                check.setChecked(role in state.roles)
                check.setEnabled(state.enabled and role != "whdload")
            self.inspect_btn.setEnabled(
                state.enabled and bool(state.source) and not state.inspection_pending()
            )
            if state.inspection_error:
                text = f"Source check failed: {state.inspection_error}"
            elif state.inspection_pending():
                text = "Checking AGS source…"
            elif state.inventory:
                text = f"AGS {state.inventory.version}"
            else:
                text = ""
            self.result_label.setText(text)
            self.result_label.setVisible(bool(text))
            sizes = (
                {p.volume.casefold(): p.size for p in state.inventory.partitions}
                if state.inventory
                else {}
            )
            for role, label in self.component_sizes.items():
                size = sizes.get(AGS_VOLUMES[role].casefold())
                label.setText(f"{size / 1024**3:.2f} GiB" if size is not None else "—")
            self.layout_label.setText(state.layout_message)
            self.layout_label.setVisible(bool(state.layout_message) and state.inventory is not None)
        finally:
            self._rendering = False

    def set_config(self, config):
        self.controller.load_ags(config)

    def get_config(self):
        return self.controller.ags_config()

    def inspection_pending(self):
        return self.controller.inspection_pending()

    def shutdown_workers(self, timeout_ms=500):
        return self.controller.shutdown_workers(timeout_ms)
