"""target capacity combo and FAT32 boot size spin"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.defaults import COMMON_DISK_SIZES
from emu68hatcher.config.partition_helpers import disk_size_for_gb


class CapacityRow(QWidget):
    """capacity and boot partition inputs above the partition table"""

    size_chosen = Signal(object)
    invalid = Signal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(16)
        self.setLayout(top_row)

        size_group = QWidget()
        size_layout = QVBoxLayout(size_group)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        size_layout.addWidget(QLabel("Target capacity"))
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.size_combo.lineEdit().setPlaceholderText("Capacity in GiB")
        self.size_combo.lineEdit().editingFinished.connect(self._on_capacity_edited)
        self.error = ""
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
        boot_layout.addWidget(self.boot_spin)
        top_row.addWidget(boot_group)

    def set_auto(self, size_bytes, label) -> None:
        """show the card's exact bytes and lock the combo"""
        self.show_capacity(size_bytes)
        self.size_combo.setEnabled(False)
        self.auto_size_label.setText("Using the selected card's exact capacity")
        self.auto_size_label.setToolTip(label)
        self.auto_size_label.setVisible(True)

    def clear_auto(self) -> bool:
        """unlock the combo; False if it was not locked"""
        if self.size_combo.isEnabled():
            return False
        self.size_combo.setEnabled(True)
        self.auto_size_label.setVisible(False)
        self.auto_size_label.clear()
        self.show_capacity(self._model.disk_size)
        return True

    def sync_boot_spin(self) -> None:
        self.boot_spin.blockSignals(True)
        self.boot_spin.setValue(self._model.boot_size // (1024 * 1024))
        self.boot_spin.blockSignals(False)

    def show_capacity(self, size):
        self.size_combo.blockSignals(True)
        index = self.size_combo.findData(size)
        if index < 0:
            self.size_combo.addItem(f"{size / 1024**3:.6f} GiB", size)
            index = self.size_combo.count() - 1
        self.size_combo.setCurrentIndex(index)
        self.size_combo.blockSignals(False)
        self.error = ""

    def _on_capacity_edited(self):
        if not self.size_combo.isEnabled():
            return
        try:
            gib = float(self.size_combo.currentText().split()[0])
            size = int(gib * 1024**3)
            if size <= 0:
                raise ValueError
        except (ValueError, IndexError, OverflowError):
            self.error = "Enter a positive image capacity in GiB."
            self.invalid.emit()
            return
        # A selected preset retains its exact byte count, not its rounded label.
        index = self.size_combo.currentIndex()
        if (
            index >= 0
            and self.size_combo.currentText() == self.size_combo.itemText(index)
            and self.size_combo.itemData(index) is not None
        ):
            size = self.size_combo.itemData(index)
        self.error = ""
        self.size_chosen.emit(size)

    def _on_disk_size_changed(self):
        size = self.size_combo.currentData()
        if size is not None:
            self.error = ""
            self.size_chosen.emit(size)
