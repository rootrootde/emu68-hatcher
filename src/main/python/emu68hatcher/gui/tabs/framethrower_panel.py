"""Framethrower / C790 native-video capture settings."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QGroupBox, QLabel, QSpinBox

from emu68hatcher.config.boot_models import ConfigTxtSettings, UnicamDevice
from emu68hatcher.gui.forms import add_form_row, form_layout
from emu68hatcher.gui.widgets import select_combo_by_data


class FramethrowerPanel(QGroupBox):
    """Unicam capture fields of ConfigTxtSettings, usable only with an RTG Workbench mode."""

    def __init__(self, parent=None):
        super().__init__("Framethrower / C790 (Unicam)", parent)
        self._rtg_enabled = False
        form = form_layout(self)

        self.framethrower_check = QCheckBox("Enable native-video capture")
        self.framethrower_check.toggled.connect(self._update_fields)
        add_form_row(
            form,
            "Configuration:",
            self.framethrower_check,
            "Captures native Amiga video through Unicam for VideoCore output.",
        )

        self.unicam_device_combo = QComboBox()
        self.unicam_device_combo.addItem("Framethrower", UnicamDevice.FRAMETHROWER.value)
        self.unicam_device_combo.addItem("C790 (HDMI to CSI)", UnicamDevice.C790.value)
        add_form_row(
            form,
            "Capture device:",
            self.unicam_device_combo,
            "Select the connected capture board. C790 requires Emu68 1.1 or later.",
        )

        self.framethrower_boot_check = QCheckBox("Start on boot")
        self.framethrower_boot_check.setChecked(True)
        add_form_row(
            form,
            "Startup:",
            self.framethrower_boot_check,
            "Starts native-video capture during Emu68 boot.",
        )

        self.framethrower_scaling_combo = QComboBox()
        self.framethrower_scaling_combo.addItem("No scaling flag", "none")
        self.framethrower_scaling_combo.addItem("Smooth", "smooth")
        self.framethrower_scaling_combo.addItem("Integer / pixel perfect", "integer")
        self.framethrower_scaling_combo.setCurrentIndex(1)
        self.framethrower_scaling_combo.currentIndexChanged.connect(self._update_fields)
        add_form_row(
            form,
            "Unicam scaling:",
            self.framethrower_scaling_combo,
            "Chooses how captured native video is scaled for HDMI output.",
        )

        self.framethrower_b_spin = QSpinBox()
        self.framethrower_b_spin.setRange(0, 1000)
        self.framethrower_b_spin.setValue(200)
        add_form_row(
            form,
            "Smooth scaling B:",
            self.framethrower_b_spin,
            "Sets the B filter coefficient used by smooth scaling.",
        )

        self.framethrower_c_spin = QSpinBox()
        self.framethrower_c_spin.setRange(0, 1000)
        self.framethrower_c_spin.setValue(400)
        add_form_row(
            form,
            "Smooth scaling C:",
            self.framethrower_c_spin,
            "Sets the C filter coefficient used by smooth scaling.",
        )

        self.framethrower_note = QLabel(
            "Native-video capture requires a VideoCore Workbench mode. "
            "C790 requires Emu68 1.1 or later. "
            "For PAL, use a fixed 50 Hz HDMI mode."
        )
        self.framethrower_note.setWordWrap(True)
        form.addRow(self.framethrower_note)

    def set_rtg_enabled(self, enabled: bool):
        self._rtg_enabled = enabled
        self._update_fields()

    def _update_fields(self, _value=None):
        rtg_enabled = self._rtg_enabled
        if not rtg_enabled and self.framethrower_check.isChecked():
            self.framethrower_check.setChecked(False)
        self.framethrower_check.setEnabled(rtg_enabled)
        framethrower = rtg_enabled and self.framethrower_check.isChecked()
        self.unicam_device_combo.setEnabled(framethrower)
        self.framethrower_boot_check.setEnabled(framethrower)
        self.framethrower_scaling_combo.setEnabled(framethrower)
        smooth = self.framethrower_scaling_combo.currentData() == "smooth"
        self.framethrower_b_spin.setEnabled(framethrower and smooth)
        self.framethrower_c_spin.setEnabled(framethrower and smooth)
        form = self.layout()
        for field in (
            self.unicam_device_combo,
            self.framethrower_boot_check,
            self.framethrower_scaling_combo,
        ):
            form.setRowVisible(field, framethrower)
        for field in (self.framethrower_b_spin, self.framethrower_c_spin):
            form.setRowVisible(field, framethrower and smooth)
        self.framethrower_note.setVisible(framethrower)

    def get_settings(self) -> dict:
        return {
            "framethrower": self.framethrower_check.isChecked(),
            "unicam_device": self.unicam_device_combo.currentData(),
            "framethrower_start_on_boot": self.framethrower_boot_check.isChecked(),
            "framethrower_scaling": self.framethrower_scaling_combo.currentData(),
            "framethrower_b": self.framethrower_b_spin.value(),
            "framethrower_c": self.framethrower_c_spin.value(),
        }

    def set_settings(self, config: ConfigTxtSettings):
        select_combo_by_data(self.unicam_device_combo, config.unicam_device.value)
        self.framethrower_check.setChecked(config.framethrower)
        self.framethrower_boot_check.setChecked(config.framethrower_start_on_boot)
        select_combo_by_data(self.framethrower_scaling_combo, config.framethrower_scaling.value)
        self.framethrower_b_spin.setValue(config.framethrower_b)
        self.framethrower_c_spin.setValue(config.framethrower_c)
        self._update_fields()
