"""partition tab - editable layout"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.schema import PartitionConfig
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.partition_editor_model import PartitionEditorModel
from emu68hatcher.gui.tabs.extra_files import ExtraFilesPanel
from emu68hatcher.gui.widgets.capacity_row import CapacityRow
from emu68hatcher.gui.widgets.partition_bar import PartitionBar
from emu68hatcher.gui.widgets.partition_table import PartitionTable


class PartitionsTab(QWidget):
    """partition layout editor"""

    def __init__(self, parent=None, *, controller):
        super().__init__(parent)
        self.controller = controller
        self._updating = False
        self._loading = False
        self._model = self.controller.model
        self.setup_ui()
        self.capacity_row.sync_boot_spin()
        self._refresh_table()
        self.controller.layout_changed.connect(self.render_snapshot)
        self.controller.extras.changed.connect(self.extras_page.update_status_cells)

    @property
    def capacity_error(self) -> str:
        return self.capacity_row.error

    @capacity_error.setter
    def capacity_error(self, value: str) -> None:
        self.capacity_row.error = value

    def render_snapshot(self):
        self._loading = True
        try:
            self.capacity_row.sync_boot_spin()
            self._refresh_table()
        finally:
            self._loading = False

    def setup_ui(self):
        layout = page_layout(self)

        # --- Disk Size + Boot Partition (side by side) ---
        self.capacity_row = CapacityRow(self._model)
        self.size_combo = self.capacity_row.size_combo
        self.auto_size_label = self.capacity_row.auto_size_label
        self.boot_spin = self.capacity_row.boot_spin
        self.capacity_row.size_chosen.connect(self._apply_disk_size_bytes)
        self.capacity_row.invalid.connect(self._on_capacity_invalid)
        self.boot_spin.valueChanged.connect(self._on_boot_size_changed)
        self.boot_spin.editingFinished.connect(self._refresh_table)

        layout.addWidget(self.capacity_row)

        # --- Partition Bar ---
        self.partition_bar = PartitionBar()
        self.partition_bar._on_resize_callback = self._on_bar_resize
        layout.addWidget(self.partition_bar)

        # --- Amiga Partitions ---
        self.amiga_group = QGroupBox("Partition layout")
        amiga_layout = QVBoxLayout(self.amiga_group)

        self.part_table = PartitionTable()
        self.part_table.device_edited.connect(self._on_device_changed)
        self.part_table.volume_edited.connect(self._on_volume_changed)
        self.part_table.size_edited.connect(self._on_size_changed)
        self.part_table.filesystem_edited.connect(self._on_fs_changed)
        self.part_table.bootable_edited.connect(self._on_bootable_changed)
        self.part_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.partition_bar.partition_clicked.connect(self.part_table.selectRow)
        amiga_layout.addWidget(self.part_table)

        button_row = QWidget()
        btn_layout = QHBoxLayout(button_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        self.add_btn = QPushButton("Add Partition")
        self.add_btn.clicked.connect(self._on_add_partition)
        btn_layout.addWidget(self.add_btn)

        self.remove_btn = QPushButton("Remove Partition")
        self.remove_btn.clicked.connect(self._on_remove_partition)
        btn_layout.addWidget(self.remove_btn)

        self.reset_btn = QPushButton("Reset to Default")
        self.reset_btn.clicked.connect(self._reset_to_default)
        btn_layout.addWidget(self.reset_btn)

        btn_layout.addStretch()
        amiga_layout.addWidget(button_row)

        self.extras_page = ExtraFilesPanel(self.controller, self.part_table)
        self.extras_page.directory_changed.connect(self._emit_layout_changed)
        self.extras_partition_combo = self.extras_page.extras_partition_combo

        layout.addWidget(self.amiga_group)

        # --- Status ---
        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        self.error_label = QLabel()
        self.error_label.setProperty("tone", "warning")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

    # ── External signals (output tab → here) ────────────────────────────

    def set_auto_disk_size(self, size_bytes, label) -> None:
        """lock disk_size + reset to the SD card's exact bytes; boot_spin stays editable"""
        self.capacity_row.set_auto(size_bytes, label)
        self.reset_btn.setEnabled(False)
        self._apply_disk_size_bytes(size_bytes)

    def clear_auto_disk_size(self) -> None:
        """Unlock without rounding or changing the current draft."""
        if self.capacity_row.clear_auto():
            self.reset_btn.setEnabled(True)

    # ── Event handlers ──────────────────────────────────────────────────

    def _apply_disk_size_bytes(self, disk_size_bytes: int) -> None:
        self._model.change_disk_size(disk_size_bytes)
        self.capacity_row.sync_boot_spin()
        self._refresh_table()

    def _on_capacity_invalid(self):
        self.error_label.setText(self.capacity_error)
        self.error_label.show()
        self.controller.changed.emit()

    def _on_boot_size_changed(self):
        self._model.set_boot_size_mb(self.boot_spin.value())
        self._update_status()
        self._emit_layout_changed()

    def _on_add_partition(self):
        if self._model.add_partition():
            self._refresh_table()

    def _on_remove_partition(self):
        row = self._selected_partition_row()
        if (
            0 <= row < len(self._model.partitions)
            and self._model.partitions[row].extra_content_directory
        ):
            if (
                QMessageBox.question(
                    self,
                    "Remove partition",
                    "Remove this partition and its extra-file association?",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        if self._model.remove_partition(row):
            self._refresh_table()

    def _on_device_changed(self, row: int, text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        self._model.set_device(row, text)
        self.extras_page.refresh()
        self._update_status()
        self._emit_layout_changed()

    def _on_volume_changed(self, row: int, text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        self._model.set_volume(row, text)
        self.extras_page.refresh()
        self._update_status()
        self.extras_page.refresh()
        self._emit_layout_changed()

    def _on_size_changed(self, row: int, text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        try:
            self._model.set_partition_size_mb(row, int(text))
        except ValueError:
            pass
        self._refresh_table()

    def _on_fs_changed(self, row: int, fs_text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        try:
            self._model.set_filesystem(row, fs_text)
        except ValueError:
            pass
        self._update_status()
        self._emit_layout_changed()

    def _on_bar_resize(self, left_idx, left_size, right_idx, right_size) -> bool:
        """resize from the bar widget drag"""
        if not self._model.resize_pair(left_idx, left_size, right_idx, right_size):
            return False
        self._refresh_table()
        return True

    def _on_selection_changed(self):
        if self._updating:
            return
        row = self._selected_partition_row()
        self._model.selected_id = self._model.partition_ids[row] if row >= 0 else None
        self._update_bar()
        self.extras_page.refresh()
        self.remove_btn.setEnabled(
            len(self._model.partitions) > 1
            and 0 <= row < len(self._model.partitions)
            and self._model.partitions[row].ags_reservation is None
        )

    def _selected_partition_row(self) -> int:
        return self.part_table.selected_row()

    def _on_bootable_changed(self, row: int, checked: bool) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        self._model.set_bootable(row, checked)
        self._refresh_table()

    # ── Table sync ──────────────────────────────────────────────────────

    def _refresh_table(self):
        """rebuild table from internal state"""
        editor_state = None
        focused = QApplication.focusWidget()
        if isinstance(focused, QLineEdit) and self.part_table.isAncestorOf(focused):
            row = self.part_table.currentRow()
            device_item = self.part_table.item(row, 0)
            if device_item is not None:
                editor_state = (
                    device_item.data(Qt.ItemDataRole.UserRole),
                    self.part_table.currentColumn(),
                    focused.text(),
                    focused.cursorPosition(),
                )
        self._updating = True
        try:
            statuses = [self.controller.extras.status(part) for part in self._model.partitions]
            self.part_table.render(self._model.partitions, statuses, self._model.partition_ids)
            if self._model.selected_id in self._model.partition_ids:
                self.part_table.selectRow(self._model.partition_ids.index(self._model.selected_id))
            else:
                self.part_table.clearSelection()
            selected = self.part_table.selected_row()
            self.remove_btn.setEnabled(
                len(self._model.partitions) > 1
                and 0 <= selected < len(self._model.partitions)
                and self._model.partitions[selected].ags_reservation is None
            )
            self.add_btn.setEnabled(self._model.can_add)
            if editor_state and editor_state[0] in self._model.partition_ids:
                identity, column, text, position = editor_state
                row = self._model.partition_ids.index(identity)
                item = self.part_table.item(row, column)
                if item is not None and item.flags() & Qt.ItemFlag.ItemIsEditable:
                    self.part_table.setCurrentCell(row, column)
                    self.part_table.editItem(item)
                    editor = QApplication.focusWidget()
                    if isinstance(editor, QLineEdit):
                        editor.setText(text)
                        editor.setCursorPosition(position)
        finally:
            self._updating = False

        self._update_status()
        self.extras_page.refresh()
        self._emit_layout_changed()

    def _emit_layout_changed(self):
        if not self._loading:
            self.controller.replan()

    def _space(self) -> tuple[int, int, int]:
        return (
            self._model.usable_space,
            self._model.allocated_space,
            self._model.free_space,
        )

    def _update_bar(self):
        """refresh the partition bar viz"""
        _usable, _allocated, free = self._space()
        selected = self._selected_partition_row()
        self.partition_bar.set_data(
            self._model.boot_size,
            self._model.partitions,
            free,
            selected,
        )

    def _update_status(self):
        """refresh status + error labels"""
        usable, allocated, free = self._space()

        used_gb = allocated / (1024**3)
        total_gb = usable / (1024**3)
        free_mb = free / (1024**2)

        self.status_label.setText(
            f"{used_gb:.2f} of {total_gb:.2f} GiB allocated "
            + (
                f"({free_mb:.0f} MiB unallocated)"
                if free >= 0
                else f"({-free_mb:.0f} MiB shortfall)"
            )
        )

        errors = self._model.errors + ([self.capacity_error] if self.capacity_error else [])
        if errors:
            self.error_label.setText("\n".join(errors))
        else:
            self.error_label.setText("")

        self.error_label.setVisible(bool(errors))
        self._update_bar()

    def _reset_to_default(self):
        """reset partitions to the default for the current disk size"""
        if any(part.ags_reservation for part in self._model.partitions):
            trial = PartitionEditorModel()
            trial.partitions = [part.model_copy(deep=True) for part in self._model.partitions]
            trial.reset(disk_size_bytes=self._model.disk_size, preserve_extra_directories=True)
            before = "\n".join(
                f"{part.device}: {part.volume} {part.size / 1024**3:.2f} GiB"
                for part in self._model.partitions
            )
            after = "\n".join(
                f"{part.device}: {part.volume} {part.size / 1024**3:.2f} GiB"
                for part in trial.partitions
            )
            errors = trial.errors
            if errors:
                QMessageBox.warning(
                    self,
                    "Reset partitions",
                    f"Current layout:\n{before}\n\nProposed layout:\n{after}"
                    + "\n\nCannot apply:\n"
                    + "\n".join(errors),
                )
                return
            if (
                QMessageBox.question(
                    self,
                    "Reset partitions",
                    f"Current layout:\n{before}\n\nProposed layout:\n{after}\n\nApply this reset?",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            self._model.load(trial.to_config())
        else:
            self._model.reset(
                disk_size_bytes=self._model.disk_size, preserve_extra_directories=True
            )
        self._scan_extra_directories()
        self.capacity_row.sync_boot_spin()
        self._refresh_table()

    # ── Config I/O ──────────────────────────────────────────────────────

    def get_config(self) -> PartitionConfig:
        """PartitionConfig from current editor state"""
        if self.capacity_error:
            raise ValueError(self.capacity_error)
        return self._model.to_config()

    def set_config(self, config: PartitionConfig | None, *, update=True):
        """populate tab from a PartitionConfig"""
        if config is None:
            return

        self._loading = True
        try:
            self._model.load(config)
            self._scan_extra_directories()

            self.capacity_row.show_capacity(config.disk_size)

            self.capacity_row.sync_boot_spin()
            self._refresh_table()
        finally:
            self._loading = False
        if update:
            self._emit_layout_changed()

    def _scan_extra_directories(self):
        self.controller.extras.scan()

    def extra_content_scan_pending(self):
        return self.extras_page.scan_pending()

    def extra_content_errors(self):
        return self.extras_page.errors()

    def shutdown_workers(self, timeout_ms=500):
        return self.controller.extras.shutdown_workers(timeout_ms)
