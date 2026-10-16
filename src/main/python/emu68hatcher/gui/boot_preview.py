"""Generated boot file preview pane."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QSplitter, QTabWidget, QVBoxLayout, QWidget


class BootPreviewPane(QWidget):
    """Read-only tabs showing the rendered boot partition files."""

    def __init__(self, parent=None):
        super().__init__(parent)
        preview_layout = QVBoxLayout(self)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(QLabel("Generated boot files"))
        preview_note = QLabel("Combined result of Emu68, Display and Software settings.")
        preview_note.setWordWrap(True)
        preview_layout.addWidget(preview_note)
        self.preview_tabs = QTabWidget()
        preview_layout.addWidget(self.preview_tabs)
        self._preview_editors: dict[str, QPlainTextEdit] = {}
        self._preview_tab_widgets: dict[str, QWidget] = {}
        self._preview_filenames: tuple[str, ...] = ()
        self.set_error("Preview is not available yet.")

    @staticmethod
    def _new_preview_editor() -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        return editor

    def _new_preview_section(self, heading: str, detail: str, filename: str) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        heading_label = QLabel(heading)
        heading_font = heading_label.font()
        heading_font.setBold(True)
        heading_label.setFont(heading_font)
        layout.addWidget(heading_label)

        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label)

        editor = self._new_preview_editor()
        self._preview_editors[filename] = editor
        layout.addWidget(editor)
        return section

    @staticmethod
    def _backup_filename(filename: str) -> str | None:
        if not filename.endswith(".txt"):
            return None
        return f"{filename[:-4]}BAK.txt"

    def _new_preview_page(self, filename: str, backup_filename: str | None) -> QWidget:
        if backup_filename is None:
            editor = self._new_preview_editor()
            self._preview_editors[filename] = editor
            return editor

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(
            self._new_preview_section(
                "First boot",
                f"{filename} is used for the first boot.",
                filename,
            )
        )
        splitter.addWidget(
            self._new_preview_section(
                "Subsequent boots",
                f"{backup_filename} becomes {filename} after the first boot.",
                backup_filename,
            )
        )
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 300])
        return splitter

    def set_files(self, files: dict[str, str]):
        selected = self.preview_tabs.tabText(self.preview_tabs.currentIndex())
        filenames = tuple(files)
        if self._preview_filenames != filenames:
            self.preview_tabs.clear()
            self._preview_editors = {}
            self._preview_tab_widgets = {}
            self._preview_filenames = filenames
            consumed: set[str] = set()
            for filename in files:
                if filename in consumed:
                    continue
                backup_filename = self._backup_filename(filename)
                if backup_filename not in files:
                    backup_filename = None
                page = self._new_preview_page(filename, backup_filename)
                self._preview_tab_widgets[filename] = page
                self.preview_tabs.addTab(page, filename)
                consumed.add(filename)
                if backup_filename is not None:
                    consumed.add(backup_filename)

        for filename, content in files.items():
            editor = self._preview_editors[filename]
            if editor.toPlainText() == content:
                continue
            cursor_position = editor.textCursor().position()
            vertical_position = editor.verticalScrollBar().value()
            horizontal_position = editor.horizontalScrollBar().value()
            editor.setPlainText(content)
            cursor = editor.textCursor()
            cursor.setPosition(min(cursor_position, len(content)))
            editor.setTextCursor(cursor)
            editor.verticalScrollBar().setValue(vertical_position)
            editor.horizontalScrollBar().setValue(horizontal_position)

        selected_page = self._preview_tab_widgets.get(selected)
        if selected_page is not None:
            self.preview_tabs.setCurrentWidget(selected_page)

    def set_error(self, message: str):
        self.preview_tabs.clear()
        self._preview_editors = {}
        self._preview_tab_widgets = {}
        self._preview_filenames = ()
        editor = self._new_preview_editor()
        editor.setPlainText(message)
        self.preview_tabs.addTab(editor, "Preview")
