"""Display output, Picasso96, and native-video capture settings."""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.boot_models import Emu68BootSettings
from emu68hatcher.config.display_models import (
    WORKBENCH_RTG_MODES,
    WorkbenchScreenMode,
)
from emu68hatcher.config.schema import DisplayConfig
from emu68hatcher.data.data_manager import load_yaml_data
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.tabs.framethrower_panel import FramethrowerPanel
from emu68hatcher.gui.tabs.picasso96_panel import Picasso96Panel
from emu68hatcher.gui.widgets import select_combo_by_data
from emu68hatcher.gui.work_areas import scroll_page, scroll_to


class DisplayTab(QWidget):
    """HDMI, Workbench RTG, and native-video settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgb2rtg_video = None
        self._hdmi_before_rgb2rtg = None
        self.hdmi_modes = []
        self.load_screen_modes()
        self.setup_ui()

    def load_screen_modes(self):
        self.hdmi_modes = [
            {
                "name": r.get("name", ""),
                "friendly": r.get("friendly_name", r.get("name", "")),
            }
            for r in load_yaml_data("screen_modes")
        ]
        self.hdmi_modes.append({"name": "Custom", "friendly": "Custom Resolution"})

    def setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(scroll_page(content))

        self.content = content
        layout = page_layout(content)

        hdmi_group = QGroupBox("HDMI output")
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

        self.rgb2rtg_hdmi_note = QLabel("HDMI mode is set by RGB2RTG under Hardware & Emu68.")
        self.rgb2rtg_hdmi_note.setProperty("tone", "muted")
        self.rgb2rtg_hdmi_note.setWordWrap(True)
        self.rgb2rtg_hdmi_note.hide()
        hdmi_layout.addWidget(self.rgb2rtg_hdmi_note)

        self.custom_res_widget = QWidget()
        custom_outer = QVBoxLayout(self.custom_res_widget)
        custom_outer.setContentsMargins(0, 0, 0, 0)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Width:"))
        self.hdmi_width_spin = QSpinBox()
        self.hdmi_width_spin.setRange(320, 1920)
        self.hdmi_width_spin.setValue(800)
        size_row.addWidget(self.hdmi_width_spin)
        size_row.addWidget(QLabel("Height:"))
        self.hdmi_height_spin = QSpinBox()
        self.hdmi_height_spin.setRange(200, 1200)
        self.hdmi_height_spin.setValue(600)
        size_row.addWidget(self.hdmi_height_spin)
        size_row.addWidget(QLabel("Hz:"))
        self.hdmi_hz_spin = QSpinBox()
        self.hdmi_hz_spin.setRange(24, 75)
        self.hdmi_hz_spin.setValue(60)
        size_row.addWidget(self.hdmi_hz_spin)
        size_row.addStretch()
        custom_outer.addLayout(size_row)

        cvt_row = QGridLayout()
        cvt_row.setColumnStretch(1, 1)
        cvt_row.setColumnStretch(3, 1)
        cvt_row.addWidget(QLabel("Aspect:"), 0, 0)
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
        cvt_row.addWidget(self.hdmi_aspect_combo, 0, 1)
        cvt_row.addWidget(QLabel("Margins:"), 0, 2)
        self.hdmi_margins_combo = QComboBox()
        self.hdmi_margins_combo.addItem("Disabled", False)
        self.hdmi_margins_combo.addItem("Enabled", True)
        cvt_row.addWidget(self.hdmi_margins_combo, 0, 3)
        cvt_row.addWidget(QLabel("Scan:"), 1, 0)
        self.hdmi_interlace_combo = QComboBox()
        self.hdmi_interlace_combo.addItem("Progressive", False)
        self.hdmi_interlace_combo.addItem("Interlace", True)
        cvt_row.addWidget(self.hdmi_interlace_combo, 1, 1)
        cvt_row.addWidget(QLabel("Blanking:"), 1, 2)
        self.hdmi_rb_combo = QComboBox()
        self.hdmi_rb_combo.addItem("Normal", False)
        self.hdmi_rb_combo.addItem("Reduced", True)
        cvt_row.addWidget(self.hdmi_rb_combo, 1, 3)
        custom_outer.addLayout(cvt_row)

        self.custom_res_widget.setVisible(False)
        hdmi_layout.addWidget(self.custom_res_widget)
        self.force_hdmi_check = QCheckBox("Force HDMI output without EDID")
        self.force_hdmi_check.setChecked(True)
        self.force_hdmi_check.setToolTip(
            "Forces HDMI output when no display or EDID is detected during boot."
        )
        hdmi_layout.addWidget(self.force_hdmi_check)

        layout.addWidget(hdmi_group)

        workbench_group = QGroupBox("Workbench screen")
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
        self.workbench_mode_note.setProperty("tone", "muted")
        workbench_layout.addWidget(self.workbench_mode_note)
        layout.addWidget(workbench_group)

        self.hdmi_width_spin.valueChanged.connect(self._rebuild_workbench_modes)
        self.hdmi_height_spin.valueChanged.connect(self._rebuild_workbench_modes)

        self.appearance_content = QWidget()
        page_layout(self.appearance_content)
        layout.addWidget(self.appearance_content)

        self.picasso96 = Picasso96Panel()
        layout.addWidget(self.picasso96)

        self.framethrower = FramethrowerPanel()
        layout.addWidget(self.framethrower)

        layout.addStretch()
        self._rebuild_workbench_modes()

    @property
    def picasso96_archive_edit(self):
        return self.picasso96.picasso96_archive_edit

    @property
    def framethrower_check(self):
        return self.framethrower.framethrower_check

    @property
    def unicam_device_combo(self):
        return self.framethrower.unicam_device_combo

    def navigate(self, section):
        scroll_to(self.appearance_content if section == "appearance" else self.hdmi_mode_combo)

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
        self.framethrower.set_rtg_enabled(self.has_rtg_workbench_mode())

    def has_rtg_workbench_mode(self) -> bool:
        return self.workbench_mode_combo.currentData() != WorkbenchScreenMode.NATIVE.value

    def get_emu68_boot_settings(self) -> dict:
        return {
            "force_hdmi": self.force_hdmi_check.isChecked(),
            **self.framethrower.get_settings(),
        }

    def set_emu68_boot_settings(self, settings: Emu68BootSettings):
        self.force_hdmi_check.setChecked(settings.config_txt.force_hdmi)
        self.framethrower.set_settings(settings.config_txt)

    def get_picasso96_archive(self) -> Path | None:
        return self.picasso96.get_archive()

    def set_picasso96_archive(self, archive):
        self.picasso96.set_archive(archive)

    def get_config(self) -> dict:
        hdmi_mode_name = self.hdmi_mode_combo.currentData() or "1280*720-50"
        return {
            "hdmi_mode": hdmi_mode_name,
            "workbench_mode": self.workbench_mode_combo.currentData(),
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
        self._on_workbench_mode_changed()
