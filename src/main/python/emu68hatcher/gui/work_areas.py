"""Page containers and compact configuration summaries."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.gui.design import FIELD_GAP, page_layout

PAGE_LABELS = {
    "overview": "Overview",
    "system": "System",
    "appearance": "Appearance",
    "software": "Software",
    "storage": "Storage",
    "review": "Review & build",
}
PAGE_DESCRIPTIONS = {
    "overview": "Updates and your current configuration.",
    "system": "Hardware, AmigaOS and network settings.",
    "appearance": "Display output and the Workbench desktop.",
    "software": "Applications and drivers. Required packages are included automatically.",
    "storage": "Output target, imported content and partition layout.",
    "review": "Check your settings before building.",
}


def scroll_page(widget):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


def scroll_to(widget):
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(widget, 0, 16)
        parent = parent.parentWidget()


class SectionPage(QTabWidget):
    """Top-level sections for System, with the same spacing as the other pages."""

    def __init__(self, sections):
        super().__init__()
        self.setDocumentMode(True)
        self.tabBar().setDrawBase(False)
        self.sections = {}
        for key, label, widget in sections:
            wrapper = QWidget()
            layout = page_layout(wrapper)
            layout.setContentsMargins(0, 20, 0, 0)
            layout.addWidget(widget)
            self.sections[key] = wrapper
            self.addTab(wrapper, label.replace("&", "&&"))

    def navigate(self, section):
        if section:
            self.setCurrentWidget(self.sections[section])


class OverviewPage(QWidget):
    def __init__(self, summary, tools):
        super().__init__()
        layout = page_layout(self)
        layout.addWidget(tools)
        layout.addWidget(summary)
        layout.addStretch()
        self.sections = {"summary": summary, "tools": tools}

    def navigate(self, section):
        scroll_to(self.sections[section])


class SummaryPage(QWidget):
    destination_requested = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self.content_layout = page_layout(self)
        self.summary_group = QGroupBox("Configuration")
        self.summary_grid = QGridLayout(self.summary_group)
        self.summary_grid.setColumnStretch(0, 1)
        self.summary_grid.setColumnStretch(1, 1)
        self.summary_values = {}
        self.content_layout.addWidget(self.summary_group)
        self.checks_group = QGroupBox("Build checks")
        checks_layout = QVBoxLayout(self.checks_group)
        self.check_status = QLabel()
        checks_layout.addWidget(self.check_status)
        self.issues = QListWidget()
        self.issues.setObjectName("checks")
        self.issues.setAccessibleName("Build checks; activate to open the setting")
        self.issues.setWordWrap(True)
        self.issues.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.issues.itemActivated.connect(self._activate)
        self.issues.itemClicked.connect(self._activate)
        checks_layout.addWidget(self.issues)
        self.content_layout.addWidget(self.checks_group)

    def show_summary(self, rows):
        for index, (title, value) in enumerate(rows):
            if title not in self.summary_values:
                cell = QWidget()
                layout = QVBoxLayout(cell)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(4)
                label = QLabel(title)
                label.setProperty("tone", "muted")
                layout.addWidget(label)
                text = QLabel()
                text.setTextFormat(Qt.TextFormat.PlainText)
                text.setWordWrap(True)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(text)
                self.summary_values[title] = text
                self.summary_grid.addWidget(cell, index // 2, index % 2)
            self.summary_values[title].setText(value)

    def _activate(self, item):
        destination = item.data(Qt.ItemDataRole.UserRole)
        if destination:
            self.destination_requested.emit(*destination)

    def show_issues(self, issues):
        self.issues.clear()
        for issue in issues:
            item = QListWidgetItem(f"{PAGE_LABELS[issue.page]} · {issue.message}")
            icon = (
                QStyle.StandardPixmap.SP_BrowserReload
                if issue.severity == "pending"
                else QStyle.StandardPixmap.SP_MessageBoxWarning
            )
            item.setIcon(self.style().standardIcon(icon))
            item.setToolTip(issue.message)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{issue.severity}: {item.text()}")
            item.setData(
                Qt.ItemDataRole.UserRole, (issue.page, issue.subpage or "", issue.field or "")
            )
            self.issues.addItem(item)
        blocking = sum(issue.severity != "warning" for issue in issues)
        self.check_status.setText(
            f"{blocking} to resolve · Select a check to open its setting"
            if blocking
            else "Ready to build" + (" · Review the warnings below" if issues else "")
        )
        self.issues.setVisible(bool(issues))
        self._fit_checks()

    def _fit_checks(self):
        self.issues.doItemsLayout()
        height = sum(max(32, self.issues.sizeHintForRow(i)) for i in range(self.issues.count()))
        self.issues.setFixedHeight(min(160, height + 4))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_checks()


class StoragePage(QWidget):
    def __init__(self, output, partitions, ags):
        super().__init__()
        layout = page_layout(self)
        layout.addWidget(output)
        self.ags_group = QGroupBox("AGS import")
        ags_layout = QVBoxLayout(self.ags_group)
        ags_layout.addWidget(ags)
        layout.addWidget(self.ags_group)

        self.source_status = QLabel()
        self.source_status.setWordWrap(True)
        self.source_status.setProperty("tone", "warning")
        partition_layout = partitions.amiga_group.layout()
        for index, widget in enumerate(
            (
                partitions.capacity_row,
                partitions.partition_bar,
                partitions.status_label,
                self.source_status,
                partitions.error_label,
            )
        ):
            partition_layout.insertWidget(index, widget)
        layout.addWidget(partitions)
        self.extras_group = QGroupBox("Extra files")
        extras_layout = QVBoxLayout(self.extras_group)
        extras_layout.setSpacing(FIELD_GAP)
        extras_layout.addWidget(partitions.extras_page)
        layout.addWidget(self.extras_group)
        layout.addStretch()
        self.sections = {
            "ags": self.ags_group,
            "partitions": partitions,
            "extras": self.extras_group,
        }
        self.controller = ags.controller
        self.controller.changed.connect(self._show_source_status)
        self._show_source_status()

    def _show_source_status(self):
        state = self.controller
        text = ""
        if state.enabled and state.inventory is None:
            text = (
                "Checking AGS sizes…"
                if state.inspection_pending()
                else "AGS sizes unconfirmed — check the source image."
            )
        self.source_status.setText(text)
        self.source_status.setVisible(bool(text))

    def navigate(self, section):
        scroll_to(self.sections[section])
