"""Picker for a user-supplied full Picasso96 archive."""

from pathlib import Path

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from emu68hatcher.gui.widgets.status_row import StatusRow


class Picasso96Panel(QGroupBox):
    """Picasso96 archive path with browse, clear and a status row."""

    def __init__(self, parent=None):
        super().__init__("Picasso96 RTG", parent)
        p96_layout = QVBoxLayout(self)
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
        self._picasso96_status = StatusRow()
        p96_layout.addWidget(self._picasso96_status)
        self._refresh_picasso96_status()

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
            self._picasso96_status.set_status(
                "info",
                "Free Picasso96 from Aminet",
                "Add the full version archive to install Picasso96 3.x instead.",
            )
            return
        archive = Path(path)
        if not archive.is_file():
            self._picasso96_status.set_status("warning", "Archive not found", path)
            return
        self._picasso96_status.set_status("ok", "Picasso96 full version", archive.name)

    def get_archive(self) -> Path | None:
        text = self.picasso96_archive_edit.text().strip()
        return Path(text) if text else None

    def set_archive(self, archive):
        self.picasso96_archive_edit.setText(str(archive) if archive else "")
        self._refresh_picasso96_status()
