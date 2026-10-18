"""RGB2RTG settings group."""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QLineEdit,
    QPushButton,
)

from emu68hatcher.config.rgb2rtg_models import RGB2RTGConfig
from emu68hatcher.gui.forms import form_layout
from emu68hatcher.gui.tabs.emu68_advanced import paired_field
from emu68hatcher.gui.widgets import select_combo_by_data


class RGB2RTGPanel(QGroupBox):
    """RGB2RTG checkbox, release archive and video mode."""

    def __init__(self, parent=None):
        super().__init__("RGB2RTG v0.76 (experimental)", parent)
        form = form_layout(self, top=True)
        self.rgb2rtg_check = QCheckBox("A1200 / PiStorm32-lite with Pi 4 or CM4")
        form.addRow(self.rgb2rtg_check)
        self.rgb2rtg_archive = QLineEdit()
        self.rgb2rtg_archive.setPlaceholderText("RGB2RTG_A1200_v0.76.7z")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_rgb2rtg)
        form.addRow("Release archive:", paired_field(self.rgb2rtg_archive, browse))
        self.rgb2rtg_video = QComboBox()
        self.rgb2rtg_video.addItem("PAL: 1080p50 (recommended)", "pal")
        self.rgb2rtg_video.addItem("NTSC: 1080p60 (recommended)", "ntsc")
        form.addRow("Amiga / HDMI:", self.rgb2rtg_video)

    def _browse_rgb2rtg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "RGB2RTG release archive", self.rgb2rtg_archive.text(), "7-Zip archive (*.7z)"
        )
        if path:
            self.rgb2rtg_archive.setText(path)

    def get_config(self) -> RGB2RTGConfig:
        return RGB2RTGConfig(
            enabled=self.rgb2rtg_check.isChecked(),
            archive=self.rgb2rtg_archive.text(),
            video=self.rgb2rtg_video.currentData(),
        )

    def set_config(self, settings: RGB2RTGConfig):
        self.rgb2rtg_check.setChecked(settings.enabled)
        self.rgb2rtg_archive.setText(str(settings.archive) if settings.archive else "")
        select_combo_by_data(self.rgb2rtg_video, settings.video)
