"""extra files panel - per-partition folder copied into the image"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from emu68hatcher.gui.design import page_layout


class ExtraFilesPanel(QWidget):
    """partition picker and folder for the extra files of each partition"""

    directory_changed = Signal()

    def __init__(self, controller, table, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._model = controller.model
        self._table = table
        # per-partition detail panel: selected row -> extra content directory picker
        self._extras_box = QWidget()
        extras_layout = QHBoxLayout(self._extras_box)
        extras_layout.setContentsMargins(0, 0, 0, 0)
        self._extras_label = QLabel("Folder:")
        extras_layout.addWidget(self._extras_label)
        self._extras_edit = QLineEdit()
        self._extras_edit.setPlaceholderText("Optional folder to copy into this partition")
        self._extras_edit.setReadOnly(True)
        extras_layout.addWidget(self._extras_edit, 1)
        self._extras_browse_btn = QPushButton("Browse...")
        self._extras_browse_btn.clicked.connect(self._browse_extras_directory)
        extras_layout.addWidget(self._extras_browse_btn)
        self._extras_clear_btn = QPushButton("Clear")
        self._extras_clear_btn.clicked.connect(self._clear_extras_directory)
        extras_layout.addWidget(self._extras_clear_btn)
        self._extras_box.setEnabled(False)
        extras_page_layout = page_layout(self)
        extras_page_layout.setSpacing(10)
        self.extras_partition_combo = QComboBox()
        self.extras_partition_combo.currentIndexChanged.connect(self._select_extra_partition)
        destination_row = QHBoxLayout()
        destination_row.addWidget(QLabel("Partition:"))
        destination_row.addWidget(self.extras_partition_combo, 1)
        extras_page_layout.addLayout(destination_row)
        extras_page_layout.addWidget(self._extras_box)
        self.extras_status = QLabel()
        self.extras_status.setWordWrap(True)
        extras_page_layout.addWidget(self.extras_status)
        self.extras_partition_combo.setToolTip(
            "Extra files are copied last. Imported AGS partitions cannot receive extra files."
        )

    def _select_extra_partition(self):
        identity = self.extras_partition_combo.currentData()
        if identity in self._model.partition_ids:
            self._table.selectRow(self._model.partition_ids.index(identity))
        else:
            self._table.clearSelection()

    def refresh(self):
        self.extras_partition_combo.blockSignals(True)
        self.extras_partition_combo.clear()
        self.extras_partition_combo.addItem("Select a partition", None)
        for part, identity in zip(self._model.partitions, self._model.partition_ids, strict=True):
            self.extras_partition_combo.addItem(f"{part.device} ({part.volume})", identity)
        self.extras_partition_combo.setCurrentIndex(
            max(0, self.extras_partition_combo.findData(self._model.selected_id))
        )
        self.extras_partition_combo.blockSignals(False)
        row = self._table.selected_row()
        if not (0 <= row < len(self._model.partitions)):
            self._extras_box.setEnabled(False)
            self._extras_edit.clear()
            self.extras_status.hide()
            return
        part = self._model.partitions[row]
        self._extras_box.setEnabled(part.ags_reservation is None)
        self._extras_edit.setText(
            str(part.extra_content_directory) if part.extra_content_directory else ""
        )
        self.extras_status.setText(self.controller.extras.status(part)[0])
        self.extras_status.setVisible(
            bool(part.extra_content_directory) or part.ags_reservation is not None
        )

    def _browse_extras_directory(self):
        row = self._table.selected_row()
        if not (0 <= row < len(self._model.partitions)):
            return
        start = self._extras_edit.text() or ""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select directory to mirror into this partition",
            start,
        )
        if not path:
            return
        self._model.set_extra_directory(row, Path(path))
        self._extras_edit.setText(path)
        self.controller.extras.scan()
        self.update_status_cells()
        self.directory_changed.emit()

    def _clear_extras_directory(self):
        row = self._table.selected_row()
        if not (0 <= row < len(self._model.partitions)):
            return
        self._model.set_extra_directory(row, None)
        self._extras_edit.clear()
        self.controller.extras.scan()
        self.update_status_cells()
        self.directory_changed.emit()

    def update_status_cells(self) -> None:
        if self._table.rowCount() != len(self._model.partitions):
            return
        for row, part in enumerate(self._model.partitions):
            text, state = self.controller.extras.status(part)
            self._table.set_extra_status(row, text, state)
        self.refresh()

    def scan_pending(self):
        return self.controller.extras.scan_pending()

    def errors(self):
        return self.controller.extras.errors()
