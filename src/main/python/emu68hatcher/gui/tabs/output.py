"""output tab - image file, image+flash, or direct-to-SD"""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.schema import OutputConfig, OutputType
from emu68hatcher.gui.workers import DiskListWorker


class OutputTab(QWidget):
    """output config tab"""

    # `object` so the byte count stays a python int - Qt would truncate
    # multi-GB values on a 32-bit signed int signal
    target_size_changed = Signal(object, str)
    target_size_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._disks: list = []
        self._disk_worker: DiskListWorker | None = None
        self._disk_workers: set[DiskListWorker] = set()
        self._disk_generation = 0
        self._pending_device: str | None = None
        self._last_emitted_device: str | None = None
        self.setup_ui()
        # disk list populates lazily - first refresh on tab show is fine

    # ------------------------------------------------------------------ UI

    def setup_ui(self):
        layout = QVBoxLayout(self)

        # --- Output mode radio group ---
        mode_group = QGroupBox("Output mode")
        mode_layout = QVBoxLayout(mode_group)
        self.mode_buttons = QButtonGroup(self)

        self.mode_img = QRadioButton("Image file (.img)")
        self.mode_img.setChecked(True)
        self.mode_buttons.addButton(self.mode_img)
        mode_layout.addWidget(self.mode_img)

        self.mode_img_flash = QRadioButton("Image file + flash to SD card")
        self.mode_buttons.addButton(self.mode_img_flash)
        mode_layout.addWidget(self.mode_img_flash)

        self.mode_device = QRadioButton("Direct to SD card (no .img file)")
        self.mode_buttons.addButton(self.mode_device)
        mode_layout.addWidget(self.mode_device)

        # one signal per click - off-edge + on-edge would otherwise both fire
        self.mode_buttons.buttonClicked.connect(self._on_mode_changed)

        layout.addWidget(mode_group)

        # --- Image file group ---
        self.image_group = QGroupBox("Image file")
        image_layout = QVBoxLayout(self.image_group)

        path_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Select output location...")
        path_row.addWidget(self.output_path)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_output)
        path_row.addWidget(self.browse_btn)
        image_layout.addLayout(path_row)

        self.sparse_cb = QCheckBox(
            "Sparse - allocates only the data actually used (saves disk space)"
        )
        self.sparse_cb.setChecked(True)
        image_layout.addWidget(self.sparse_cb)

        layout.addWidget(self.image_group)

        # --- SD card group ---
        self.disk_group = QGroupBox("SD card")
        disk_layout = QVBoxLayout(self.disk_group)

        disk_row = QHBoxLayout()
        disk_row.addWidget(QLabel("Disk:"))
        self.disk_combo = QComboBox()
        self.disk_combo.setMinimumWidth(380)
        self.disk_combo.currentIndexChanged.connect(self._on_disk_selected)
        disk_row.addWidget(self.disk_combo, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_disks)
        disk_row.addWidget(self.refresh_btn)
        disk_layout.addLayout(disk_row)

        self.verify_after_flash_cb = QCheckBox("Verify after writing")
        self.verify_after_flash_cb.setChecked(True)
        self.verify_after_flash_cb.setToolTip(
            "Write the image first, then read back and verify the blocks written. "
            "All-zero image blocks are skipped and not verified. "
            "Uncheck for faster write-only flashing."
        )
        disk_layout.addWidget(self.verify_after_flash_cb)

        warning = QLabel("⚠ This will ERASE the selected disk!")
        warning.setStyleSheet("color: #b00; font-weight: bold;")
        warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        disk_layout.addWidget(warning)

        layout.addWidget(self.disk_group)
        layout.addStretch()

        self._on_mode_changed()  # apply initial visibility

    # ------------------------------------------------------------------ behaviour

    def _on_mode_changed(self):
        """show/hide groups based on selected mode"""
        is_img_only = self.mode_img.isChecked()
        is_flash = self.mode_img_flash.isChecked()
        is_device = self.mode_device.isChecked()

        self.image_group.setVisible(is_img_only or is_flash)
        self.disk_group.setVisible(is_flash or is_device)
        self.verify_after_flash_cb.setVisible(is_flash)

        if is_device or is_flash:
            # both modes write to a real card, so disk_size is locked to it
            self.refresh_disks()
            self._emit_target_size()
        else:
            self._last_emitted_device = None
            self.target_size_cleared.emit()

    def _on_disk_selected(self):
        self._emit_target_size()

    def _browse_output(self):
        current = self.output_path.text().strip()
        start = current or str(Path.home() / "amiga.img")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output Location",
            start,
            "Disk Images (*.img);;All Files (*)",
        )
        if path:
            self.output_path.setText(path)

    def refresh_disks(self):
        """spawn DiskListWorker, fill combo on result"""
        self._disk_generation += 1
        generation = self._disk_generation
        for worker in tuple(self._disk_workers):
            if worker.isRunning():
                worker.requestInterruption()
        if self._pending_device is None:
            self._pending_device = self.disk_combo.currentData()
        # block signals: clear() flips the index and would emit a stale
        # target_size_changed(0, "") before the new list lands
        self.disk_combo.blockSignals(True)
        self.disk_combo.clear()
        self.disk_combo.addItem("Scanning…", None)
        self.disk_combo.blockSignals(False)
        self._disk_worker = DiskListWorker(self)
        worker = self._disk_worker
        self._disk_workers.add(worker)
        worker.disks_loaded.connect(lambda disks, g=generation: self._accept_disks(g, disks))
        worker.load_error.connect(lambda message, g=generation: self._accept_disk_error(g, message))
        worker.finished.connect(lambda w=worker: self._disk_worker_finished(w))
        worker.start()

    def _disk_worker_finished(self, worker: DiskListWorker) -> None:
        self._disk_workers.discard(worker)
        if worker is self._disk_worker:
            self._disk_worker = None

    def _accept_disks(self, generation: int, disks: list) -> None:
        if generation == self._disk_generation:
            self._on_disks_loaded(disks)

    def _accept_disk_error(self, generation: int, message: str) -> None:
        if generation != self._disk_generation:
            return
        self._pending_device = None
        self._disks = []
        self.disk_combo.blockSignals(True)
        self.disk_combo.clear()
        self.disk_combo.addItem(f"(disk scan failed: {message})", None)
        self.disk_combo.blockSignals(False)
        self._emit_target_size(force=True)

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        workers = tuple(worker for worker in self._disk_workers if worker.isRunning())
        for worker in workers:
            worker.requestInterruption()
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            remaining = max(0, int((deadline - monotonic()) * 1000))
            worker.wait(remaining)
        return not any(worker.isRunning() for worker in workers)

    @Slot(list)
    def _on_disks_loaded(self, disks: list):
        self._disks = disks
        desired = self._pending_device
        self.disk_combo.blockSignals(True)
        self.disk_combo.clear()
        if not disks:
            self.disk_combo.addItem(
                "(no removable disks found - insert an SD card and refresh)", None
            )
        else:
            self.disk_combo.addItem("(select a disk)", None)
            for d in disks:
                self.disk_combo.addItem(d.display_label, d.device)
            if desired:
                index = self.disk_combo.findData(desired)
                self.disk_combo.setCurrentIndex(index if index >= 0 else 0)
        self.disk_combo.blockSignals(False)
        self._pending_device = None
        self._emit_target_size()

    def _emit_target_size(self, *, force: bool = False):
        """DEVICE or IMG+flash: push the picked card's size so partitions can auto-size"""
        if not (self.mode_device.isChecked() or self.mode_img_flash.isChecked()):
            return
        device = self.disk_combo.currentData()
        info = next((d for d in self._disks if d.device == device), None)
        if info is None:
            self._last_emitted_device = None
            self.target_size_cleared.emit()
            return
        if not force and device == self._last_emitted_device:
            return
        self._last_emitted_device = device
        self.target_size_changed.emit(info.size_bytes, info.display_label)

    # ------------------------------------------------------------------ config IO

    def get_config(self) -> dict:
        if self.mode_device.isChecked():
            return {
                "type": OutputType.DEVICE.value,
                "path": self.disk_combo.currentData() or "",
                "sparse": False,
                "flash_target": None,
                "verify_after_flash": self.verify_after_flash_cb.isChecked(),
            }
        flash_target = self.disk_combo.currentData() if self.mode_img_flash.isChecked() else None
        return {
            "type": OutputType.IMG.value,
            "path": self.output_path.text(),
            "sparse": self.sparse_cb.isChecked(),
            "flash_target": flash_target,
            "verify_after_flash": self.verify_after_flash_cb.isChecked(),
        }

    def set_config(self, config: OutputConfig | None) -> None:
        if config is None:
            return
        self.verify_after_flash_cb.setChecked(config.verify_after_flash)
        if config.type == OutputType.DEVICE:
            self.mode_device.setChecked(True)
        elif config.flash_target:
            self.mode_img_flash.setChecked(True)
            if config.path:
                self.output_path.setText(str(config.path))
            self.sparse_cb.setChecked(config.sparse)
        else:
            self.mode_img.setChecked(True)
            if config.path:
                self.output_path.setText(str(config.path))
            self.sparse_cb.setChecked(config.sparse)

        # raw device names are reused and may point at another disk next time
        self.disk_combo.blockSignals(True)
        self.disk_combo.setCurrentIndex(0 if self.disk_combo.count() else -1)
        self.disk_combo.blockSignals(False)
        self._pending_device = None
        # setChecked does not emit buttonClicked
        self._on_mode_changed()

    def needs_disk_target(self) -> bool:
        return (self.mode_device.isChecked() or self.mode_img_flash.isChecked()) and not (
            self.disk_combo.currentData()
        )
