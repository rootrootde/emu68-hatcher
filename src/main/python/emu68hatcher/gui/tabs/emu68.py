"""Emu68 release, hardware and boot settings."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.boot_models import Emu68BootSettings
from emu68hatcher.config.rgb2rtg_models import RGB2RTGConfig
from emu68hatcher.config.schema import Emu68Version
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.forms import add_form_row, form_layout
from emu68hatcher.gui.tabs.emu68_advanced import AdvancedBootSettings
from emu68hatcher.gui.tabs.rgb2rtg_panel import RGB2RTGPanel
from emu68hatcher.gui.widgets import select_combo_by_data, set_release_default
from emu68hatcher.gui.work_areas import scroll_page


class Emu68Tab(QWidget):
    """Emu68 release, hardware and boot settings."""

    emu68_version_changed = Signal(str)

    def __init__(
        self,
        emu68_version: Emu68Version = Emu68Version.V1_0_7,
        parent=None,
    ):
        super().__init__(parent)
        self.emu68_version = emu68_version
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        settings_pane = QWidget()
        settings_layout = QVBoxLayout(settings_pane)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)

        settings_header = QHBoxLayout()
        settings_header.setContentsMargins(0, 0, 0, 0)
        self.advanced_button = QPushButton("Show advanced settings")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._set_advanced_visible)
        settings_header.addWidget(self.advanced_button)
        settings_header.addStretch()

        self.content = QWidget()
        self.content_layout = page_layout(self.content)
        self.settings_scroll = scroll_page(self.content)
        settings_layout.addWidget(self.settings_scroll)

        self.release_group = self._create_release_group()
        self.content_layout.addWidget(self.release_group)

        self.settings_warning = QLabel(
            "⚠ These settings are experimental and may prevent Emu68 from booting."
        )
        self.settings_warning.setWordWrap(True)
        self.settings_warning.setProperty("alert", True)
        self.settings_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.hardware_group = self._create_hardware_group()
        self.content_layout.addWidget(self.hardware_group)
        self.rgb2rtg_panel = RGB2RTGPanel()
        self.rgb2rtg_check = self.rgb2rtg_panel.rgb2rtg_check
        self.rgb2rtg_archive = self.rgb2rtg_panel.rgb2rtg_archive
        self.rgb2rtg_video = self.rgb2rtg_panel.rgb2rtg_video
        self.rgb2rtg_check.toggled.connect(self._rgb2rtg_toggled)
        self.content_layout.addWidget(self.rgb2rtg_panel)
        self.content_layout.addLayout(settings_header)
        self.content_layout.addWidget(self.settings_warning)

        self.advanced = AdvancedBootSettings(
            self.storage_timing_combo,
            self.emu68_version,
            lambda: self.advanced_button.setChecked(True),
        )
        self.emmc_unit_combo = self.advanced.emmc_unit_combo
        self.extra_config_edit = self.advanced.extra_config_edit
        self._advanced_widgets = (self.settings_warning, *self.advanced.groups)
        for widget in self._advanced_widgets[1:]:
            self.content_layout.addWidget(widget)
        self.content_layout.addStretch()

        layout.addWidget(settings_pane)

        self._set_advanced_visible(False)
        self._normalise_control_widths()
        self.advanced.sync_storage_timing_from_details()
        self.set_emu68_version(self.emu68_version)
        self.advanced.update_custom_fields()

    def _normalise_control_widths(self):
        for combo in self.findChildren(QComboBox):
            combo.setMinimumWidth(180)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for spin in self.findChildren(QSpinBox):
            spin.setMinimumWidth(92)

    def _set_advanced_visible(self, visible: bool):
        for widget in self._advanced_widgets:
            widget.setVisible(visible)
        self.advanced_button.setText(
            "Hide advanced settings" if visible else "Show advanced settings"
        )

    def _create_release_group(self) -> QGroupBox:
        release_group = QGroupBox("Emu68 release")
        layout = QVBoxLayout(release_group)

        self.release_button_group = QButtonGroup(self)
        self.release_radio_stable = QRadioButton("1.0.7 (stable)")
        self.release_radio_beta = QRadioButton("1.1.0-beta.1 (beta)")
        self.release_radio_alpha = QRadioButton("1.1.0-alpha.1 (legacy)")
        self.release_radio_stable.setChecked(True)
        self.release_button_group.addButton(self.release_radio_stable)
        self.release_button_group.addButton(self.release_radio_beta)
        self.release_button_group.addButton(self.release_radio_alpha)
        self.release_button_group.buttonClicked.connect(self._select_emu68_version)
        layout.addWidget(self.release_radio_stable)
        layout.addWidget(self.release_radio_beta)
        layout.addWidget(self.release_radio_alpha)
        return release_group

    def _rgb2rtg_toggled(self, enabled):
        self.release_radio_stable.setEnabled(not enabled)
        self.release_radio_alpha.setEnabled(not enabled)
        if enabled:
            self.set_emu68_version(Emu68Version.V1_1_0_BETA_1)

    def get_rgb2rtg(self) -> RGB2RTGConfig:
        return self.rgb2rtg_panel.get_config()

    def set_rgb2rtg(self, settings: RGB2RTGConfig):
        self.rgb2rtg_panel.set_config(settings)
        self._rgb2rtg_toggled(settings.enabled)

    def _create_hardware_group(self) -> QGroupBox:
        hardware_group = QGroupBox("Hardware")
        form = form_layout(hardware_group, top=True)
        labels: list[QLabel] = []

        self.antenna_combo = QComboBox()
        self.antenna_combo.addItem("Release default", "default")
        self.antenna_combo.addItem("Internal (ant1)", "internal")
        self.antenna_combo.addItem("External (ant2)", "external")
        add_form_row(
            form,
            "CM4 antenna:",
            self.antenna_combo,
            "Selects the internal antenna or external connector on CM4 boards.",
            labels=labels,
        )

        self.storage_timing_combo = QComboBox()
        self.storage_timing_combo.addItem("Compatible (recommended)", "compatible")
        self.storage_timing_combo.addItem("Standard", "standard")
        self.storage_timing_combo.addItem("Custom (advanced)", "custom")
        add_form_row(
            form,
            "Storage timing:",
            self.storage_timing_combo,
            "Compatible uses low-speed SD and eMMC timing. Standard uses normal clocks.",
            labels=labels,
        )

        return hardware_group

    def set_emu68_version(self, version: Emu68Version | str):
        self.emu68_version = version if isinstance(version, Emu68Version) else Emu68Version(version)
        if self.emu68_version == Emu68Version.V1_1_0_BETA_1:
            self.release_radio_beta.setChecked(True)
        elif self.emu68_version == Emu68Version.V1_1_0_ALPHA_1:
            self.release_radio_alpha.setChecked(True)
        else:
            self.release_radio_stable.setChecked(True)

        if self.emu68_version == Emu68Version.V1_1_0_ALPHA_1:
            defaults = (2, 0, 0, "enabled", "external", "disabled")
        elif self.emu68_version == Emu68Version.V1_1_0_BETA_1:
            defaults = (2, 0, 0, "disabled", "internal", "disabled")
        else:
            defaults = (1, 2048, 32, "disabled", "internal", "first_boot")

        warning, memory, gpu, turbo, antenna, bus_test = defaults
        self.advanced.set_release_defaults(
            self.emu68_version, warning, memory, gpu, turbo, bus_test
        )
        set_release_default(self.antenna_combo, antenna)
        self.advanced.update_custom_fields()
        self.emu68_version_changed.emit(self.emu68_version.value)

    def get_emu68_version(self) -> Emu68Version:
        if self.release_radio_beta.isChecked():
            return Emu68Version.V1_1_0_BETA_1
        if self.release_radio_alpha.isChecked():
            return Emu68Version.V1_1_0_ALPHA_1
        return Emu68Version.V1_0_7

    def _select_emu68_version(self, _button=None):
        self.set_emu68_version(self.get_emu68_version())

    def get_settings(self) -> dict:
        return self.advanced.get_settings(antenna=self.antenna_combo.currentData())

    def set_settings(self, settings: Emu68BootSettings):
        select_combo_by_data(self.antenna_combo, settings.config_txt.antenna.value)
        self.advanced.set_settings(settings)
