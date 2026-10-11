"""partition tab - editable layout"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.defaults import (
    COMMON_DISK_SIZES,
)
from emu68hatcher.config.partition_helpers import (
    disk_size_for_gb,
)
from emu68hatcher.config.schema import PartitionConfig
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.partition_editor_model import PartitionEditorModel
from emu68hatcher.gui.storage_controller import StorageController
from emu68hatcher.gui.widgets.partition_bar import PartitionBar
from emu68hatcher.gui.widgets.partition_table import PartitionTable


class PartitionsTab(QWidget):
    """partition layout editor"""

    layout_changed = Signal()

    def __init__(self, parent=None, *, controller=None):
        super().__init__(parent)
        self.controller = controller or StorageController(self)
        self._updating = False
        self._loading = False
        self._model = self.controller.model
        self.setup_ui()
        self._sync_boot_spin()
        self._refresh_table()
        self.controller.layout_changed.connect(self.render_snapshot)
        self.controller.extras.changed.connect(self._update_extra_status_cells)

    def render_snapshot(self):
        self._loading = True
        try:
            self._sync_boot_spin()
            self._refresh_table()
        finally:
            self._loading = False

    def setup_ui(self):
        layout = page_layout(self)

        # --- Disk Size + Boot Partition (side by side) ---
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(16)

        self.capacity_row = QWidget()
        self.capacity_row.setLayout(top_row)
        size_group = QWidget()
        size_layout = QVBoxLayout(size_group)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        size_layout.addWidget(QLabel("Target capacity"))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.lineEdit().setPlaceholderText("Capacity in GiB")
        self.size_combo.lineEdit().editingFinished.connect(self._on_capacity_edited)
        self.capacity_error = ""
        for gb in COMMON_DISK_SIZES:
            size = disk_size_for_gb(gb)
            self.size_combo.addItem(f"{size / 1024**3:.3f} GiB ({gb} GB card)", size)
        self.size_combo.setCurrentIndex(COMMON_DISK_SIZES.index(64))
        self.size_combo.currentIndexChanged.connect(self._on_disk_size_changed)
        size_layout.addWidget(self.size_combo)
        # shown only when output mode locks the size (Direct-to-SD card)
        self.auto_size_label = QLabel()
        self.auto_size_label.setProperty("tone", "muted")
        self.auto_size_label.setWordWrap(True)
        self.auto_size_label.setVisible(False)
        size_layout.addWidget(self.auto_size_label)
        top_row.addWidget(size_group)

        boot_group = QWidget()
        boot_layout = QVBoxLayout(boot_group)
        boot_layout.setContentsMargins(0, 0, 0, 0)
        boot_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        boot_layout.addWidget(QLabel("FAT32 boot (EMU68BOOT)"))
        self.boot_spin = QSpinBox()
        self.boot_spin.setRange(128, 16384)
        self.boot_spin.setSuffix(" MiB")
        self.boot_spin.setSingleStep(64)
        self.boot_spin.setValue(self._model.boot_size // (1024 * 1024))
        # no keyboard tracking: typed digits commit on enter/focus-out only. arrow
        # steps get the cheap bar/status preview; the table rebuild waits for commit
        self.boot_spin.setKeyboardTracking(False)
        self.boot_spin.valueChanged.connect(self._on_boot_size_changed)
        self.boot_spin.editingFinished.connect(self._refresh_table)
        boot_layout.addWidget(self.boot_spin)
        top_row.addWidget(boot_group)

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
        self.extras_page = QWidget()
        extras_page_layout = page_layout(self.extras_page)
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
        self._show_capacity(size_bytes)
        self.size_combo.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.auto_size_label.setText("Using the selected card's exact capacity")
        self.auto_size_label.setToolTip(label)
        self.auto_size_label.setVisible(True)
        self._apply_disk_size_bytes(size_bytes)

    def clear_auto_disk_size(self) -> None:
        """Unlock without rounding or changing the current draft."""
        if not self.size_combo.isEnabled():
            self.size_combo.setEnabled(True)
            self.reset_btn.setEnabled(True)
            self.auto_size_label.setVisible(False)
            self.auto_size_label.clear()
            self._show_capacity(self._model.disk_size)

    # ── Event handlers ──────────────────────────────────────────────────

    def _sync_boot_spin(self) -> None:
        self.boot_spin.blockSignals(True)
        self.boot_spin.setValue(self._model.boot_size // (1024 * 1024))
        self.boot_spin.blockSignals(False)

    def _apply_disk_size_bytes(self, disk_size_bytes: int) -> None:
        self._model.change_disk_size(disk_size_bytes)
        self._sync_boot_spin()
        self._refresh_table()

    def _show_capacity(self, size):
        self.size_combo.blockSignals(True)
        index = self.size_combo.findData(size)
        if index < 0:
            self.size_combo.addItem(f"{size / 1024**3:.6f} GiB", size)
            index = self.size_combo.count() - 1
        self.size_combo.setCurrentIndex(index)
        self.size_combo.blockSignals(False)
        self.capacity_error = ""

    def _on_capacity_edited(self):
        if not self.size_combo.isEnabled():
            return
        try:
            gib = float(self.size_combo.currentText().split()[0])
            size = int(gib * 1024**3)
            if size <= 0:
                raise ValueError
        except (ValueError, IndexError, OverflowError):
            self.capacity_error = "Enter a positive image capacity in GiB."
            self.error_label.setText(self.capacity_error)
            self.error_label.show()
            self.controller.changed.emit()
            return
        # A selected preset retains its exact byte count, not its rounded label.
        index = self.size_combo.currentIndex()
        if index >= 0 and self.size_combo.currentText() == self.size_combo.itemText(index):
            size = self.size_combo.itemData(index)
        self.capacity_error = ""
        self._apply_disk_size_bytes(size)

    def _on_disk_size_changed(self):
        size = self.size_combo.currentData()
        if size is not None:
            self.capacity_error = ""
            self._apply_disk_size_bytes(size)

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
        self._refresh_extras_panel()
        self._update_status()
        self._emit_layout_changed()

    def _on_volume_changed(self, row: int, text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        self._model.set_volume(row, text)
        self._refresh_extras_panel()
        self._update_status()
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

    def _on_bar_resize(self, left_idx, left_size, right_idx, right_size):
        """resize from the bar widget drag"""
        self._model.resize_pair(left_idx, left_size, right_idx, right_size)
        self._refresh_table()

    def _on_selection_changed(self):
        if self._updating:
            return
        row = self._selected_partition_row()
        self._model.selected_id = self._model.partition_ids[row] if row >= 0 else None
        self._update_bar()
        self._refresh_extras_panel()
        self.remove_btn.setEnabled(
            len(self._model.partitions) > 1
            and 0 <= row < len(self._model.partitions)
            and self._model.partitions[row].ags_reservation is None
        )

    def _selected_partition_row(self) -> int:
        return self.part_table.selected_row()

    def _select_extra_partition(self):
        identity = self.extras_partition_combo.currentData()
        if identity in self._model.partition_ids:
            self.part_table.selectRow(self._model.partition_ids.index(identity))
        else:
            self.part_table.clearSelection()

    def _refresh_extras_panel(self):
        self.extras_partition_combo.blockSignals(True)
        self.extras_partition_combo.clear()
        self.extras_partition_combo.addItem("Select a partition", None)
        for part, identity in zip(self._model.partitions, self._model.partition_ids, strict=True):
            self.extras_partition_combo.addItem(f"{part.device} ({part.volume})", identity)
        self.extras_partition_combo.setCurrentIndex(
            max(0, self.extras_partition_combo.findData(self._model.selected_id))
        )
        self.extras_partition_combo.blockSignals(False)
        row = self._selected_partition_row()
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
        self.extras_status.setText(self._extra_status(part)[0])
        self.extras_status.setVisible(
            bool(part.extra_content_directory) or part.ags_reservation is not None
        )

    def _browse_extras_directory(self):
        row = self._selected_partition_row()
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
        self._scan_extra_directories()
        self._update_extra_status_cells()
        self._emit_layout_changed()

    def _clear_extras_directory(self):
        row = self._selected_partition_row()
        if not (0 <= row < len(self._model.partitions)):
            return
        self._model.set_extra_directory(row, None)
        self._extras_edit.clear()
        self._scan_extra_directories()
        self._update_extra_status_cells()
        self._emit_layout_changed()

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
            statuses = [self._extra_status(part) for part in self._model.partitions]
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
        self._refresh_extras_panel()
        self._emit_layout_changed()

    def _emit_layout_changed(self):
        if not self._loading:
            self.controller.replan()
            self.layout_changed.emit()

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
        self._sync_boot_spin()
        self._refresh_table()

    # ── Config I/O ──────────────────────────────────────────────────────

    def get_config(self) -> PartitionConfig:
        """PartitionConfig from current editor state"""
        if self.capacity_error:
            raise ValueError(self.capacity_error)
        return self._model.to_config()

    def focus_disk_size(self):
        self.size_combo.setFocus()
        if self.size_combo.isEnabled():
            self.size_combo.showPopup()

    def set_config(self, config: PartitionConfig | None, *, update=True):
        """populate tab from a PartitionConfig"""
        if config is None:
            return

        self._loading = True
        try:
            self._model.load(config)
            self._scan_extra_directories()

            self._show_capacity(config.disk_size)

            self._sync_boot_spin()
            self._refresh_table()
        finally:
            self._loading = False
        if update:
            self._emit_layout_changed()

    def _scan_extra_directories(self):
        self.controller.extras.scan()

    def _extra_status(self, part):
        return self.controller.extras.status(part)

    def _update_extra_status_cells(self) -> None:
        if self.part_table.rowCount() != len(self._model.partitions):
            return
        for row, part in enumerate(self._model.partitions):
            text, state = self._extra_status(part)
            self.part_table.set_extra_status(row, text, state)
        self._refresh_extras_panel()

    def extra_content_scan_pending(self):
        return self.controller.extras.scan_pending()

    def extra_content_errors(self):
        return self.controller.extras.errors()

    def shutdown_workers(self, timeout_ms=500):
        return self.controller.extras.shutdown_workers(timeout_ms)
