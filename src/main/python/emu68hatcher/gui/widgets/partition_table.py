"""Partition table rendering and typed edit signals."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from emu68hatcher.config.partition_models import AmigaPartition, Filesystem

COL_DEVICE = 0
COL_VOLUME = 1
COL_SIZE = 2
COL_FS = 3
COL_BOOTABLE = 4
COL_EXTRA = 5
COL_AGS = 6
MIN_VISIBLE_ROWS = 2

_EXTRA_COLORS = {
    "ok": QColor("#17823b"),
    "warning": QColor("#a66a00"),
    "error": QColor("#c62828"),
}


class PartitionTable(QTableWidget):
    device_edited = Signal(int, str)
    volume_edited = Signal(int, str)
    size_edited = Signal(int, str)
    filesystem_edited = Signal(int, str)
    bootable_edited = Signal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rendering = False
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels(
            ["Device", "Volume", "Size (MB)", "Filesystem", "Boot", "Extra / usable", "Source"]
        )
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(
            COL_BOOTABLE,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cellChanged.connect(self._cell_changed)

    def render(
        self,
        partitions: list[AmigaPartition],
        extra_statuses: list[tuple[str, str | None]] | None = None,
    ) -> None:
        self._rendering = True
        try:
            self.setRowCount(len(partitions))
            for row, partition in enumerate(partitions):
                reserved = partition.ags_reservation
                self.setItem(row, COL_DEVICE, QTableWidgetItem(partition.device))
                volume_item = QTableWidgetItem(partition.volume)
                if reserved:
                    volume_item.setFlags(volume_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(row, COL_VOLUME, volume_item)
                size_item = QTableWidgetItem(str(round(partition.size / (1024 * 1024))))
                size_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if reserved:
                    size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(row, COL_SIZE, size_item)
                self.setCellWidget(row, COL_FS, self._filesystem_combo(row, partition))
                self.setCellWidget(row, COL_BOOTABLE, self._bootable_widget(row, partition))
                text, state = extra_statuses[row] if extra_statuses else ("", None)
                self.set_extra_status(row, text, state)
                label = "AGS image · fixed size" if reserved else ""
                role_item = QTableWidgetItem(label)
                role_item.setFlags(role_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                role_item.setToolTip(
                    "Select Change AGS content to include or remove this partition."
                    if reserved
                    else ""
                )
                self.setItem(row, COL_AGS, role_item)
        finally:
            self._rendering = False
        self._update_minimum_height()

    def set_extra_status(self, row: int, text: str, state: str | None) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setToolTip(text)
        color = _EXTRA_COLORS.get(state or "")
        if color is not None:
            item.setForeground(QBrush(color))
        self.setItem(row, COL_EXTRA, item)

    def _update_minimum_height(self) -> None:
        row_heights = [self.rowHeight(row) for row in range(min(self.rowCount(), MIN_VISIBLE_ROWS))]
        row_heights.extend(
            [self.verticalHeader().defaultSectionSize()] * (MIN_VISIBLE_ROWS - len(row_heights))
        )
        header_height = self.horizontalHeader().sizeHint().height()
        content_height = header_height + sum(row_heights) + 2 * self.frameWidth()
        self.setMinimumHeight(max(content_height, self.minimumSizeHint().height()))

    def selected_row(self) -> int:
        rows = self.selectionModel().selectedRows() if self.selectionModel() else []
        return rows[0].row() if rows else -1

    def _filesystem_combo(self, row: int, partition: AmigaPartition) -> QComboBox:
        combo = QComboBox()
        for filesystem in Filesystem:
            combo.addItem(filesystem.value)
        combo.setCurrentText(partition.filesystem.value)
        combo.setEnabled(partition.ags_reservation is None)
        combo.currentTextChanged.connect(
            lambda text, current=row: self.filesystem_edited.emit(current, text)
        )
        return combo

    def _bootable_widget(self, row: int, partition: AmigaPartition) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        checkbox = QCheckBox()
        checkbox.setChecked(partition.bootable)
        checkbox.setEnabled(partition.ags_reservation is None)
        checkbox.stateChanged.connect(
            lambda state, current=row: self.bootable_edited.emit(current, state == 2)
        )
        layout.addWidget(checkbox)
        return widget

    def _cell_changed(self, row: int, column: int) -> None:
        if self._rendering:
            return
        item = self.item(row, column)
        if item is None:
            return
        text = item.text()
        if column == COL_DEVICE:
            self.device_edited.emit(row, text)
        elif column == COL_VOLUME:
            self.volume_edited.emit(row, text)
        elif column == COL_SIZE:
            self.size_edited.emit(row, text)
