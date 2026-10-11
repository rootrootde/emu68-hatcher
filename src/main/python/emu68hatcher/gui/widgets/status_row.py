"""One status line: drawn icon, bold title, muted detail and optional trailing controls."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from emu68hatcher.gui.design import set_status_icon


class StatusRow(QWidget):
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.icon = QLabel()
        set_status_icon(self.icon, "pending")
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setProperty("tone", "muted")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.detail.setOpenExternalLinks(True)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.trailing = QHBoxLayout()
        self.trailing.setSpacing(8)
        layout.addLayout(self.trailing)

    def set_status(self, state, title=None, detail=""):
        set_status_icon(self.icon, state, title or self.title.text())
        if title is not None:
            self.title.setText(title)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
