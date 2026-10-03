"""Display output, Picasso96, and native-video capture settings."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.boot_models import Emu68BootSettings, UnicamDevice
from emu68hatcher.config.display_models import (
    WORKBENCH_RTG_MODES,
    WorkbenchScreenMode,
    WorkbenchTheme,
)
from emu68hatcher.config.schema import DisplayConfig
from emu68hatcher.gui.widgets import select_combo_by_data


def _read_picasso96_version(archive: Path) -> str | None:
    """best-effort read of Picasso96Install/Version from inside the .lha"""
    import subprocess

    from emu68hatcher.utils.host_tools import find_7z

    sevenz = find_7z()
    if sevenz is None:
        return None
    try:
        result = subprocess.run(
            [str(sevenz), "e", "-so", str(archive), "Picasso96Install/Version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


class DisplayTab(QWidget):
    """HDMI, Workbench RTG, and native-video settings."""

    workbench_mode_changed = Signal(bool)
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgb2rtg_video = None
        self._hdmi_before_rgb2rtg = None
        self.hdmi_modes = []
        # Retain inactive theme selections when loading and saving older configs.
        self.workbench_theme = WorkbenchTheme.DEFAULT
        self.load_screen_modes()
        self.setup_ui()

    def load_screen_modes(self):
        try:
            from emu68hatcher.data.data_manager import load_yaml_data

            modes = load_yaml_data("screen_modes")
            self.hdmi_modes = [
                {
                    "name": r.get("name", ""),
                    "friendly": r.get("friendly_name", r.get("name", "")),
                }
                for r in modes
            ]
            self.hdmi_modes.append({"name": "Custom", "friendly": "Custom Resolution"})
        except Exception:
            self.hdmi_modes = [
                {"name": "Auto", "friendly": "Automatic"},
                {"name": "1280*720-50", "friendly": "720p 50Hz (PAL)"},
                {"name": "1280*720-60", "friendly": "720p 60Hz (NTSC)"},
                {"name": "1920*1080-50", "friendly": "1080p 50Hz"},
                {"name": "1920*1080-60", "friendly": "1080p 60Hz"},
                {"name": "Custom", "friendly": "Custom Resolution"},
            ]

    def setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout = QVBoxLayout(content)

        header = QHBoxLayout()
        header.addWidget(QLabel("Display settings"))
        header.addStretch()
        self.advanced_button = QPushButton("Show Advanced")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._set_advanced_visible)
        header.addWidget(self.advanced_button)
        layout.addLayout(header)

        hdmi_group = QGroupBox("HDMI Output Mode (Pi - Monitor)")
        hdmi_layout = QVBoxLayout(hdmi_group)

        hdmi_h = QHBoxLayout()
        hdmi_h.addWidget(QLabel("Output Mode:"))
        self.hdmi_mode_combo = QComboBox()
        for mode in self.hdmi_modes:
            self.hdmi_mode_combo.addItem(mode["friendly"], mode["name"])
        for i, mode in enumerate(self.hdmi_modes):
            if "720" in mode["friendly"] and "50" in mode["friendly"]:
                self.hdmi_mode_combo.setCurrentIndex(i)
                break
        self.hdmi_mode_combo.currentIndexChanged.connect(self.on_hdmi_mode_changed)
        hdmi_h.addWidget(self.hdmi_mode_combo)
        hdmi_h.addStretch()
        hdmi_layout.addLayout(hdmi_h)

        self.rgb2rtg_hdmi_note = QLabel("HDMI mode is set by RGB2RTG on the Emu68 tab.")
        self.rgb2rtg_hdmi_note.setWordWrap(True)
        self.rgb2rtg_hdmi_note.hide()
        hdmi_layout.addWidget(self.rgb2rtg_hdmi_note)

        self.custom_res_widget = QWidget()
        custom_outer = QVBoxLayout(self.custom_res_widget)
        custom_outer.setContentsMargins(0, 0, 0, 0)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Width:"))
        self.hdmi_width_spin = QSpinBox()
        self.hdmi_width_spin.setRange(640, 1920)
        self.hdmi_width_spin.setValue(800)
        size_row.addWidget(self.hdmi_width_spin)
        size_row.addWidget(QLabel("Height:"))
        self.hdmi_height_spin = QSpinBox()
        self.hdmi_height_spin.setRange(480, 1200)
        self.hdmi_height_spin.setValue(600)
        size_row.addWidget(self.hdmi_height_spin)
        size_row.addWidget(QLabel("Hz:"))
        self.hdmi_hz_spin = QSpinBox()
        self.hdmi_hz_spin.setRange(50, 75)
        self.hdmi_hz_spin.setValue(60)
        size_row.addWidget(self.hdmi_hz_spin)
        size_row.addStretch()
        custom_outer.addLayout(size_row)

        cvt_row = QHBoxLayout()
        cvt_row.addWidget(QLabel("Aspect:"))
        self.hdmi_aspect_combo = QComboBox()
        for value, label in (
            (3, "16:9"),
            (1, "4:3"),
            (2, "14:9"),
            (4, "5:4"),
            (5, "16:10"),
            (6, "15:9"),
        ):
            self.hdmi_aspect_combo.addItem(label, value)
        cvt_row.addWidget(self.hdmi_aspect_combo)
        cvt_row.addWidget(QLabel("Margins:"))
        self.hdmi_margins_combo = QComboBox()
        self.hdmi_margins_combo.addItem("Disabled", False)
        self.hdmi_margins_combo.addItem("Enabled", True)
        cvt_row.addWidget(self.hdmi_margins_combo)
        cvt_row.addWidget(QLabel("Scan:"))
        self.hdmi_interlace_combo = QComboBox()
        self.hdmi_interlace_combo.addItem("Progressive", False)
        self.hdmi_interlace_combo.addItem("Interlace", True)
        cvt_row.addWidget(self.hdmi_interlace_combo)
        cvt_row.addWidget(QLabel("Blanking:"))
        self.hdmi_rb_combo = QComboBox()
        self.hdmi_rb_combo.addItem("Normal", False)
        self.hdmi_rb_combo.addItem("Reduced", True)
        cvt_row.addWidget(self.hdmi_rb_combo)
        cvt_row.addStretch()
        custom_outer.addLayout(cvt_row)

        self.custom_res_widget.setVisible(False)
        hdmi_layout.addWidget(self.custom_res_widget)

        layout.addWidget(hdmi_group)

        workbench_group = QGroupBox("Workbench Screen Mode")
        workbench_layout = QVBoxLayout(workbench_group)
        workbench_row = QHBoxLayout()
        workbench_row.addWidget(QLabel("Display Mode:"))
        self.workbench_mode_combo = QComboBox()
        self.workbench_mode_combo.currentIndexChanged.connect(self._on_workbench_mode_changed)
        workbench_row.addWidget(self.workbench_mode_combo)
        workbench_row.addStretch()
        workbench_layout.addLayout(workbench_row)
        self.workbench_mode_note = QLabel(
            "VideoCore modes are written to ScreenMode.prefs during the build. "
            "Native mode keeps the ScreenMode setup window on first boot."
        )
        self.workbench_mode_note.setWordWrap(True)
        workbench_layout.addWidget(self.workbench_mode_note)
        layout.addWidget(workbench_group)

        self.hdmi_width_spin.valueChanged.connect(self._rebuild_workbench_modes)
        self.hdmi_height_spin.valueChanged.connect(self._rebuild_workbench_modes)
        self._rebuild_workbench_modes()

        p96_group = QGroupBox("Picasso96 RTG")
        p96_layout = QVBoxLayout(p96_group)
        p96_row = QHBoxLayout()
        p96_row.addWidget(QLabel("Full version archive:"))
        self.picasso96_archive_edit = QLineEdit()
        self.picasso96_archive_edit.setPlaceholderText(
            "Picasso96.lha (leave empty for the default version)"
        )
        self.picasso96_archive_edit.setReadOnly(True)
        p96_row.addWidget(self.picasso96_archive_edit, 1)
        p96_browse = QPushButton("Browse...")
        p96_browse.clicked.connect(self._browse_picasso96_archive)
        p96_row.addWidget(p96_browse)
        p96_clear = QPushButton("Clear")
        p96_clear.clicked.connect(self._clear_picasso96_archive)
        p96_row.addWidget(p96_clear)
        p96_layout.addLayout(p96_row)
        self._picasso96_status_label = QLabel("(Default version)")
        p96_layout.addWidget(self._picasso96_status_label)
        layout.addWidget(p96_group)

        self.framethrower_group = self._create_framethrower_group()
        layout.addWidget(self.framethrower_group)

        self.hdmi_behavior_group = self._create_hdmi_behavior_group()
        layout.addWidget(self.hdmi_behavior_group)

        layout.addStretch()
        self._connect_settings_signals(content)
        self._set_advanced_visible(False)
        self._update_framethrower_fields()

    @staticmethod
    def _form_layout(parent: QWidget) -> QFormLayout:
        form = QFormLayout(parent)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        return form

    @staticmethod
    def _add_form_row(form: QFormLayout, text: str, field: QWidget, help_text: str):
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setToolTip(help_text)
        label.setAccessibleDescription(help_text)
        field.setToolTip(help_text)
        field.setAccessibleDescription(help_text)
        form.addRow(label, field)

    def _create_framethrower_group(self) -> QGroupBox:
        group = QGroupBox("Framethrower / C790 (Unicam)")
        form = self._form_layout(group)

        self.framethrower_check = QCheckBox("Enable native-video capture")
        self.framethrower_check.toggled.connect(self._update_framethrower_fields)
        self._add_form_row(
            form,
            "Configuration:",
            self.framethrower_check,
            "Captures native Amiga video through Unicam for VideoCore output.",
        )

        self.unicam_device_combo = QComboBox()
        self.unicam_device_combo.addItem("Framethrower", UnicamDevice.FRAMETHROWER.value)
        self.unicam_device_combo.addItem("C790 (HDMI to CSI)", UnicamDevice.C790.value)
        self._add_form_row(
            form,
            "Capture device:",
            self.unicam_device_combo,
            "Select the connected capture board. C790 requires Emu68 1.1 or later.",
        )

        self.framethrower_boot_check = QCheckBox("Start on boot")
        self.framethrower_boot_check.setChecked(True)
        self._add_form_row(
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
        self.framethrower_scaling_combo.currentIndexChanged.connect(
            self._update_framethrower_fields
        )
        self._add_form_row(
            form,
            "Unicam scaling:",
            self.framethrower_scaling_combo,
            "Chooses how captured native video is scaled for HDMI output.",
        )

        self.framethrower_b_spin = QSpinBox()
        self.framethrower_b_spin.setRange(0, 1000)
        self.framethrower_b_spin.setValue(200)
        self._add_form_row(
            form,
            "Smooth scaling B:",
            self.framethrower_b_spin,
            "Sets the B filter coefficient used by smooth scaling.",
        )

        self.framethrower_c_spin = QSpinBox()
        self.framethrower_c_spin.setRange(0, 1000)
        self.framethrower_c_spin.setValue(400)
        self._add_form_row(
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
        return group

    def _create_hdmi_behavior_group(self) -> QGroupBox:
        group = QGroupBox("Advanced display settings")
        form = self._form_layout(group)
        self.force_hdmi_check = QCheckBox("Force HDMI output without EDID")
        self.force_hdmi_check.setChecked(True)
        self._add_form_row(
            form,
            "HDMI hotplug:",
            self.force_hdmi_check,
            "Forces HDMI output when no display or EDID is detected during boot.",
        )
        return group

    def _connect_settings_signals(self, content: QWidget):
        for combo in content.findChildren(QComboBox):
            combo.currentIndexChanged.connect(self._emit_settings_changed)
        for check in content.findChildren(QCheckBox):
            check.toggled.connect(self._emit_settings_changed)
        for spin in content.findChildren(QSpinBox):
            spin.valueChanged.connect(self._emit_settings_changed)
        self.picasso96_archive_edit.textChanged.connect(self._emit_settings_changed)

    def _emit_settings_changed(self, _value=None):
        self.settings_changed.emit()

    def _set_advanced_visible(self, visible: bool):
        self.hdmi_behavior_group.setVisible(visible)
        self.advanced_button.setText("Show Basic" if visible else "Show Advanced")

    def on_hdmi_mode_changed(self):
        mode_name = self.hdmi_mode_combo.currentData()
        self.custom_res_widget.setVisible(mode_name == "Custom")
        self._rebuild_workbench_modes()

    def set_rgb2rtg_video(self, video: str | None):
        if video is not None:
            if self._rgb2rtg_video is None:
                self._hdmi_before_rgb2rtg = self.hdmi_mode_combo.currentData()
            self._rgb2rtg_video = video
            mode = "1920*1080-60" if video == "ntsc" else "1920*1080-50"
            select_combo_by_data(self.hdmi_mode_combo, mode)
        else:
            self._rgb2rtg_video = None
            if self._hdmi_before_rgb2rtg is not None:
                select_combo_by_data(self.hdmi_mode_combo, self._hdmi_before_rgb2rtg)
                self._hdmi_before_rgb2rtg = None
        self.hdmi_mode_combo.setEnabled(video is None)
        self.rgb2rtg_hdmi_note.setVisible(video is not None)

    def _hdmi_bounds(self) -> tuple[int, int] | None:
        mode_name = self.hdmi_mode_combo.currentData()
        if mode_name == "Custom":
            return self.hdmi_width_spin.value(), self.hdmi_height_spin.value()
        size = str(mode_name).split("-", 1)[0]
        try:
            width, height = size.split("*", 1)
            return int(width), int(height)
        except ValueError:
            return None

    def _rebuild_workbench_modes(self, _value=None):
        if not hasattr(self, "workbench_mode_combo"):
            return
        selected = self.workbench_mode_combo.currentData()
        if selected is None:
            selected = WorkbenchScreenMode.VIDEOCORE_1280X720.value
        bounds = self._hdmi_bounds()

        self.workbench_mode_combo.blockSignals(True)
        self.workbench_mode_combo.clear()
        self.workbench_mode_combo.addItem(
            "Native Amiga mode (choose on first boot)",
            WorkbenchScreenMode.NATIVE.value,
        )
        for mode in WORKBENCH_RTG_MODES:
            if bounds and (mode.width > bounds[0] or mode.height > bounds[1]):
                continue
            self.workbench_mode_combo.addItem(mode.label, mode.mode.value)

        index = self.workbench_mode_combo.findData(selected)
        if index < 0:
            index = self.workbench_mode_combo.count() - 1
        self.workbench_mode_combo.setCurrentIndex(max(index, 0))
        self.workbench_mode_combo.blockSignals(False)
        self._on_workbench_mode_changed()

    def _on_workbench_mode_changed(self, _index=None):
        self._update_framethrower_fields()
        self.workbench_mode_changed.emit(self.has_rtg_workbench_mode())

    def has_rtg_workbench_mode(self) -> bool:
        return self.workbench_mode_combo.currentData() != WorkbenchScreenMode.NATIVE.value

    def _update_framethrower_fields(self, _value=None):
        if not hasattr(self, "framethrower_check"):
            return
        rtg_enabled = self.has_rtg_workbench_mode()
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
        self.framethrower_note.setVisible(framethrower)

    def get_emu68_boot_settings(self) -> dict:
        return {
            "force_hdmi": self.force_hdmi_check.isChecked(),
            "framethrower": self.framethrower_check.isChecked(),
            "unicam_device": self.unicam_device_combo.currentData(),
            "framethrower_start_on_boot": self.framethrower_boot_check.isChecked(),
            "framethrower_scaling": self.framethrower_scaling_combo.currentData(),
            "framethrower_b": self.framethrower_b_spin.value(),
            "framethrower_c": self.framethrower_c_spin.value(),
        }

    def set_emu68_boot_settings(self, settings: Emu68BootSettings):
        config = settings.config_txt
        self.force_hdmi_check.setChecked(config.force_hdmi)
        select_combo_by_data(self.unicam_device_combo, config.unicam_device.value)
        self.framethrower_check.setChecked(config.framethrower)
        self.framethrower_boot_check.setChecked(config.framethrower_start_on_boot)
        select_combo_by_data(self.framethrower_scaling_combo, config.framethrower_scaling.value)
        self.framethrower_b_spin.setValue(config.framethrower_b)
        self.framethrower_c_spin.setValue(config.framethrower_c)
        self._update_framethrower_fields()

    def _browse_picasso96_archive(self):
        from PySide6.QtWidgets import QFileDialog

        start = self.picasso96_archive_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Picasso96 archive",
            start,
            "Picasso96 archive (*.lha *.LHA);;All files (*.*)",
        )
        if path:
            self.picasso96_archive_edit.setText(path)
            self._refresh_picasso96_status()

    def _clear_picasso96_archive(self):
        self.picasso96_archive_edit.clear()
        self._refresh_picasso96_status()

    def _refresh_picasso96_status(self):
        path = self.picasso96_archive_edit.text().strip()
        if not path:
            self._picasso96_status_label.setText("(Default version)")
            return
        name = Path(path).name
        version = _read_picasso96_version(Path(path))
        if version:
            self._picasso96_status_label.setText(f"(Full version {version}: {name})")
        else:
            self._picasso96_status_label.setText(f"(Full version: {name})")

    def get_picasso96_archive(self) -> Path | None:
        text = self.picasso96_archive_edit.text().strip()
        return Path(text) if text else None

    def set_picasso96_archive(self, archive):
        self.picasso96_archive_edit.setText(str(archive) if archive else "")
        self._refresh_picasso96_status()

    def get_config(self) -> dict:
        hdmi_mode_name = self.hdmi_mode_combo.currentData() or "1280*720-50"
        return {
            "hdmi_mode": hdmi_mode_name,
            "workbench_mode": self.workbench_mode_combo.currentData(),
            "workbench_theme": self.workbench_theme.value,
            "width": self.hdmi_width_spin.value(),
            "height": self.hdmi_height_spin.value(),
            "framerate": self.hdmi_hz_spin.value(),
            "aspect_ratio": self.hdmi_aspect_combo.currentData(),
            "margins": self.hdmi_margins_combo.currentData(),
            "interlace": self.hdmi_interlace_combo.currentData(),
            "reduced_blanking": self.hdmi_rb_combo.currentData(),
        }

    def set_config(self, config: DisplayConfig):
        self._hdmi_before_rgb2rtg = None
        hdmi_mode = config.hdmi_mode or "1280*720-50"
        select_combo_by_data(self.hdmi_mode_combo, hdmi_mode)
        if config.custom:
            self.hdmi_width_spin.setValue(config.custom.width)
            self.hdmi_height_spin.setValue(config.custom.height)
            self.hdmi_hz_spin.setValue(config.custom.framerate)
            select_combo_by_data(self.hdmi_aspect_combo, config.custom.aspect_ratio)
            select_combo_by_data(self.hdmi_margins_combo, config.custom.margins)
            select_combo_by_data(self.hdmi_interlace_combo, config.custom.interlace)
            select_combo_by_data(self.hdmi_rb_combo, config.custom.reduced_blanking)
        self._rebuild_workbench_modes()
        select_combo_by_data(self.workbench_mode_combo, config.workbench_mode.value)
        self.workbench_theme = config.workbench_theme
        self._on_workbench_mode_changed()
