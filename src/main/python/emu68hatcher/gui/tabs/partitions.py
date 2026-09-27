"""partition tab - editable layout"""

import os
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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

from emu68hatcher.builder.staging.tree_copy import TreeUsage
from emu68hatcher.config.defaults import (
    COMMON_DISK_SIZES,
)
from emu68hatcher.config.partition_helpers import (
    disk_size_for_gb,
    usable_partition_content_size,
)
from emu68hatcher.config.schema import PartitionConfig
from emu68hatcher.gui.partition_editor_model import PartitionEditorModel
from emu68hatcher.gui.widgets.partition_bar import PartitionBar
from emu68hatcher.gui.widgets.partition_table import PartitionTable
from emu68hatcher.gui.workers import ExtraContentSizeWorker


class PartitionsTab(QWidget):
    """partition layout editor"""

    layout_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        self._loading = False
        self._extra_workers: set[ExtraContentSizeWorker] = set()
        self._extra_generation = 0
        self._extra_pending: set[str] = set()
        self._extra_usage: dict[str, TreeUsage] = {}
        self._extra_scan_errors: dict[str, str] = {}
        self._model = PartitionEditorModel(64)
        self.setup_ui()
        self._sync_boot_spin()
        self._refresh_table()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # --- Disk Size + Boot Partition (side by side) ---
        top_row = QHBoxLayout()

        size_group = QGroupBox("Disk Size")
        size_layout = QVBoxLayout(size_group)
        self.size_combo = QComboBox()
        for gb in COMMON_DISK_SIZES:
            self.size_combo.addItem(f"{gb} GB", gb)
        self.size_combo.setCurrentIndex(COMMON_DISK_SIZES.index(64))
        self.size_combo.currentIndexChanged.connect(self._on_disk_size_changed)
        size_layout.addWidget(self.size_combo)
        # shown only when output mode locks the size (Direct-to-SD card)
        self.auto_size_label = QLabel()
        self.auto_size_label.setStyleSheet("color: #666; font-style: italic;")
        self.auto_size_label.setVisible(False)
        size_layout.addWidget(self.auto_size_label)
        top_row.addWidget(size_group)

        boot_group = QGroupBox("Boot Partition (EMU68BOOT - FAT32)")
        boot_layout = QHBoxLayout(boot_group)
        boot_layout.addWidget(QLabel("Size:"))
        self.boot_spin = QSpinBox()
        self.boot_spin.setRange(128, 16384)
        self.boot_spin.setSuffix(" MB")
        self.boot_spin.setSingleStep(64)
        self.boot_spin.setValue(self._model.boot_size // (1024 * 1024))
        # no keyboard tracking: typed digits commit on enter/focus-out only. arrow
        # steps get the cheap bar/status preview; the table rebuild waits for commit
        self.boot_spin.setKeyboardTracking(False)
        self.boot_spin.valueChanged.connect(self._on_boot_size_changed)
        self.boot_spin.editingFinished.connect(self._refresh_table)
        boot_layout.addWidget(self.boot_spin)
        top_row.addWidget(boot_group)

        layout.addLayout(top_row)

        # --- Partition Bar ---
        self.partition_bar = PartitionBar()
        self.partition_bar._on_resize_callback = self._on_bar_resize
        layout.addWidget(self.partition_bar)

        # --- Amiga Partitions ---
        amiga_group = QGroupBox("Amiga Partitions")
        amiga_layout = QVBoxLayout(amiga_group)

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
        btn_layout.setContentsMargins(0, 16, 0, 0)
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
        extras_layout.setContentsMargins(0, 6, 0, 0)
        self._extras_label = QLabel("Extra content directory:")
        extras_layout.addWidget(self._extras_label)
        self._extras_edit = QLineEdit()
        self._extras_edit.setPlaceholderText("(optional) contents mirrored into this partition")
        self._extras_edit.setReadOnly(True)
        extras_layout.addWidget(self._extras_edit, 1)
        self._extras_browse_btn = QPushButton("Browse...")
        self._extras_browse_btn.clicked.connect(self._browse_extras_directory)
        extras_layout.addWidget(self._extras_browse_btn)
        self._extras_clear_btn = QPushButton("Clear")
        self._extras_clear_btn.clicked.connect(self._clear_extras_directory)
        extras_layout.addWidget(self._extras_clear_btn)
        self._extras_box.setEnabled(False)
        amiga_layout.addWidget(self._extras_box)

        layout.addWidget(amiga_group)

        # --- Status ---
        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

    # ── External signals (output tab → here) ────────────────────────────

    def set_auto_disk_size(self, size_bytes, label) -> None:
        """lock disk_size + reset to the SD card's exact bytes; boot_spin stays editable"""
        gb = max(1, size_bytes // (1024**3))  # for combo display only
        existing = [self.size_combo.itemData(i) for i in range(self.size_combo.count())]
        self.size_combo.blockSignals(True)
        if gb not in existing:
            self.size_combo.addItem(f"{gb} GB", gb)
        idx = next(
            (i for i in range(self.size_combo.count()) if self.size_combo.itemData(i) == gb), -1
        )
        if idx >= 0:
            self.size_combo.setCurrentIndex(idx)
        self.size_combo.blockSignals(False)
        self.size_combo.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.auto_size_label.setText(f"Auto: {label}")
        self.auto_size_label.setVisible(True)
        self._apply_disk_size_bytes(size_bytes)

    def clear_auto_disk_size(self) -> None:
        """unlock disk_size combo + reset, re-apply the GB-snapped layout"""
        if not self.size_combo.isEnabled():
            self.size_combo.setEnabled(True)
            self.reset_btn.setEnabled(True)
            self.auto_size_label.setVisible(False)
            self.auto_size_label.clear()
            self._on_disk_size_changed()  # snap layout to the GB combo's current value

    # ── Event handlers ──────────────────────────────────────────────────

    def _sync_boot_spin(self) -> None:
        self.boot_spin.blockSignals(True)
        self.boot_spin.setValue(self._model.boot_size // (1024 * 1024))
        self.boot_spin.blockSignals(False)

    def _apply_disk_size_bytes(self, disk_size_bytes: int) -> None:
        self._model.change_disk_size(disk_size_bytes)
        self._sync_boot_spin()
        self._refresh_table()

    def _on_disk_size_changed(self):
        gb = self.size_combo.currentData()
        if gb is None:
            return
        self._model.change_disk_size(disk_size_for_gb(gb))
        self._sync_boot_spin()
        self._refresh_table()

    def _on_boot_size_changed(self):
        self._model.set_boot_size_mb(self.boot_spin.value())
        self._update_status()
        self._emit_layout_changed()

    def _on_add_partition(self):
        if self._model.add_partition():
            self._refresh_table()

    def _on_remove_partition(self):
        if self._model.remove_partition(self.part_table.currentRow()):
            self._refresh_table()

    def _on_device_changed(self, row: int, text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        self._model.set_device(row, text)
        self._update_status()
        self._emit_layout_changed()

    def _on_volume_changed(self, row: int, text: str) -> None:
        if self._updating or row < 0 or row >= len(self._model.partitions):
            return
        self._model.set_volume(row, text)
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
        self._update_bar()
        self._refresh_extras_panel()
        row = self._selected_partition_row()
        self.remove_btn.setEnabled(
            len(self._model.partitions) > 1
            and 0 <= row < len(self._model.partitions)
            and self._model.partitions[row].ags_reservation is None
        )

    def _selected_partition_row(self) -> int:
        return self.part_table.selected_row()

    def _refresh_extras_panel(self):
        row = self._selected_partition_row()
        if not (0 <= row < len(self._model.partitions)):
            self._extras_box.setEnabled(False)
            self._extras_edit.clear()
            self._extras_label.setText("Extra content directory:")
            return
        part = self._model.partitions[row]
        self._extras_box.setEnabled(part.ags_reservation is None)
        self._extras_label.setText(f"Extra content for {part.device} ({part.volume}):")
        self._extras_edit.setText(
            str(part.extra_content_directory) if part.extra_content_directory else ""
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
        self._updating = True
        try:
            statuses = [self._extra_status(part) for part in self._model.partitions]
            self.part_table.render(self._model.partitions, statuses)
            selected = self.part_table.selected_row()
            self.remove_btn.setEnabled(
                len(self._model.partitions) > 1
                and 0 <= selected < len(self._model.partitions)
                and self._model.partitions[selected].ags_reservation is None
            )
            self.add_btn.setEnabled(self._model.can_add)
        finally:
            self._updating = False

        self._update_status()
        self._refresh_extras_panel()
        self._emit_layout_changed()

    def _emit_layout_changed(self):
        if not self._loading:
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
        free = max(0, free)
        selected = self.part_table.currentRow()
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
            f"Amiga space: {used_gb:.2f} GB used / {total_gb:.2f} GB total ({free_mb:.0f} MB free)"
        )

        errors = self._model.errors
        if errors:
            self.error_label.setText("\n".join(errors))
        else:
            self.error_label.setText("")

        self._update_bar()

    def _reset_to_default(self):
        """reset partitions to the default for the current disk size"""
        gb = self.size_combo.currentData()
        if gb is None:
            gb = 8
        if any(part.ags_reservation for part in self._model.partitions):
            trial = PartitionEditorModel(gb)
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
            self._model = trial
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
        return self._model.to_config()

    def set_config(self, config: PartitionConfig | None):
        """populate tab from a PartitionConfig"""
        if config is None:
            return

        self._loading = True
        try:
            self._model.load(config)
            self._scan_extra_directories()

            approx_gb = config.disk_size / (1_000_000_000 * 0.95)
            closest_gb = min(COMMON_DISK_SIZES, key=lambda x: abs(x - approx_gb))
            idx = COMMON_DISK_SIZES.index(closest_gb)
            self.size_combo.blockSignals(True)
            self.size_combo.setCurrentIndex(idx)
            self.size_combo.blockSignals(False)

            self._sync_boot_spin()
            self._refresh_table()
        finally:
            self._loading = False
        self._emit_layout_changed()

    @staticmethod
    def _extra_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(path.expanduser()))

    def _extra_directories(self) -> dict[str, Path]:
        directories: dict[str, Path] = {}
        for part in self._model.partitions:
            if part.extra_content_directory is None:
                continue
            path = Path(part.extra_content_directory).expanduser()
            directories[self._extra_key(path)] = Path(self._extra_key(path))
        return directories

    def _scan_extra_directories(self) -> None:
        self._extra_generation += 1
        generation = self._extra_generation
        for worker in self._extra_workers:
            if worker.isRunning():
                worker.requestInterruption()

        directories = self._extra_directories()
        self._extra_pending = set(directories)
        for key in directories:
            self._extra_usage.pop(key, None)
            self._extra_scan_errors.pop(key, None)
        if not directories:
            return

        worker = ExtraContentSizeWorker(list(directories.values()), self)
        self._extra_workers.add(worker)
        worker.size_found.connect(
            lambda path, usage, current=generation: self._accept_extra_usage(current, path, usage)
        )
        worker.scan_error.connect(
            lambda path, error, current=generation: self._accept_extra_error(current, path, error)
        )
        worker.finished.connect(
            lambda current_worker=worker, current=generation: self._extra_worker_finished(
                current_worker, current
            )
        )
        worker.start()

    def _accept_extra_usage(self, generation: int, path: str, usage: TreeUsage) -> None:
        if generation != self._extra_generation:
            return
        key = self._extra_key(Path(path))
        self._extra_usage[key] = usage
        self._extra_scan_errors.pop(key, None)
        self._extra_pending.discard(key)
        self._update_extra_status_cells()

    def _accept_extra_error(self, generation: int, path: str, error: str) -> None:
        if generation != self._extra_generation:
            return
        key = self._extra_key(Path(path))
        self._extra_scan_errors[key] = error
        self._extra_pending.discard(key)
        self._update_extra_status_cells()

    def _extra_worker_finished(
        self,
        worker: ExtraContentSizeWorker,
        generation: int,
    ) -> None:
        self._extra_workers.discard(worker)
        worker.deleteLater()
        if generation == self._extra_generation:
            for path in worker.directories:
                self._extra_pending.discard(self._extra_key(path))
            self._update_extra_status_cells()

    @staticmethod
    def _format_extra_size(size: int) -> str:
        if size >= 1024**3:
            return f"{size / 1024**3:.1f} GiB"
        if size >= 1024**2:
            return f"{size / 1024**2:.1f} MiB"
        return f"{size / 1024:.1f} KiB"

    def _extra_status(self, part) -> tuple[str, str | None]:
        if part.extra_content_directory is None:
            return "", None
        path = Path(part.extra_content_directory).expanduser()
        key = self._extra_key(path)
        if key in self._extra_pending:
            return "checking...", None
        if not path.exists() or not path.is_dir():
            return "folder not found", "warning"
        if key in self._extra_scan_errors:
            return "could not read folder", "error"
        usage = self._extra_usage.get(key)
        if usage is None:
            return "not checked", "warning"

        required = usage.estimated_bytes
        usable = usable_partition_content_size(part.size)
        text = f"{self._format_extra_size(required)} / {self._format_extra_size(usable)}"
        if required > usable:
            return f"{text} - too large", "error"
        return f"{text} - fits", "ok"

    def _update_extra_status_cells(self) -> None:
        if self.part_table.rowCount() != len(self._model.partitions):
            return
        for row, part in enumerate(self._model.partitions):
            text, state = self._extra_status(part)
            self.part_table.set_extra_status(row, text, state)

    def extra_content_scan_pending(self) -> bool:
        return bool(self._extra_pending.intersection(self._extra_directories()))

    def extra_content_errors(self) -> list[str]:
        errors: list[str] = []
        for part in self._model.partitions:
            if part.extra_content_directory is None:
                continue
            path = Path(part.extra_content_directory).expanduser()
            if not path.exists() or not path.is_dir():
                continue
            key = self._extra_key(path)
            scan_error = self._extra_scan_errors.get(key)
            if scan_error:
                errors.append(f"{part.device} ({part.volume}): cannot read {path}: {scan_error}")
                continue
            usage = self._extra_usage.get(key)
            if usage is None:
                errors.append(f"{part.device} ({part.volume}): folder size was not checked")
                continue
            usable = usable_partition_content_size(part.size)
            if usage.estimated_bytes > usable:
                errors.append(
                    f"{part.device} ({part.volume}): {self._format_extra_size(usage.estimated_bytes)} "
                    f"selected, but only {self._format_extra_size(usable)} is usable"
                )
        return errors

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        workers = tuple(worker for worker in self._extra_workers if worker.isRunning())
        for worker in workers:
            worker.requestInterruption()
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)
