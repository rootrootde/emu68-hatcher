"""Shared spacing, colors and navigation icons for the work areas."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)

SECTION_GAP = 20
FIELD_GAP = 10


def page_layout(widget):
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SECTION_GAP)
    return layout


def style_sections(root):
    for scroll in root.findChildren(QScrollArea):
        scroll.viewport().setAutoFillBackground(False)
        if scroll.widget() is not None:
            scroll.widget().setAutoFillBackground(False)
    for group in root.findChildren(QGroupBox):
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = group.layout()
        if layout is not None:
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(FIELD_GAP)
            if isinstance(layout, QFormLayout):
                layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)


def set_tone(widget, tone):
    widget.setProperty("tone", tone)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


_ICON_PATHS = {
    "overview": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "system": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="10" y="10" width="4" height="4"/>',
    "appearance": '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    "software": '<path d="m12 2 9 5v10l-9 5-9-5V7l9-5Zm0 10v10M3 7l9 5 9-5M7.5 4.5l9 5v5"/>',
    "storage": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 15h16m-4 3h1M8 7h8"/>',
    "review": '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 14l3 3 5-6"/>',
}


def navigation_icon(name, color, pixel_ratio=1.0):
    icon = QIcon()
    for mode, stroke in ((QIcon.Mode.Normal, color), (QIcon.Mode.Selected, "#ffffff")):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{stroke}" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round">' + _ICON_PATHS[name] + "</svg>"
        )
        pixmap = QPixmap(QSize(22, 22) * pixel_ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(svg.encode()).render(painter)
        painter.end()
        # Render in physical pixels before assigning the logical display scale.
        pixmap.setDevicePixelRatio(pixel_ratio)
        icon.addPixmap(pixmap, mode)
    return icon


def apply_design(root, sidebar=None):
    dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
    background, panel, text, muted, border, hover = (
        ("#1e222b", "#282e39", "#edf0f5", "#b1bac9", "#404958", "#343e4e")
        if dark
        else ("#f3f5f9", "#ffffff", "#202b3d", "#56647a", "#dce2eb", "#e7edf6")
    )
    palette = QPalette(QApplication.palette())
    for role, color in (
        (QPalette.ColorRole.Window, background),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, panel),
        (QPalette.ColorRole.AlternateBase, hover),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, panel),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.Mid, border),
        (QPalette.ColorRole.Highlight, "#365fbe"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, muted),
        (QPalette.ColorRole.Link, "#91b3ff" if dark else "#365fbe"),
    ):
        palette.setColor(role, color)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    root.setPalette(palette)
    warning = "#ffba86" if dark else "#a13e18"
    success = "#91d4ae" if dark else "#21633f"
    alert_text, alert_background, alert_border = (
        ("#ffaaaa", "#39282e", "#98505b") if dark else ("#a32132", "#fff1f2", "#df9aa3")
    )
    root.setStyleSheet(f"""
        QWidget#workspace {{ background: {background}; color: {text}; }}
        QLabel, QGroupBox {{ color: {text}; }}
        QLabel[tone="muted"] {{ color: {muted}; }}
        QLabel[tone="warning"] {{ color: {warning}; }}
        QLabel[tone="success"] {{ color: {success}; }}
        QLabel[alert="true"] {{
            color: {alert_text}; background: {alert_background};
            border: 1px solid {alert_border}; border-radius: 6px;
            padding: 12px; font-weight: 600;
        }}
        QLabel[heading="page"] {{ font-size: 24px; font-weight: 600; }}
        QLabel[heading="brand"] {{ font-size: 17px; font-weight: 600; }}
        QScrollArea, QStackedWidget {{ border: none; background: transparent; }}
        QGroupBox {{
            background: {panel}; border: 1px solid {border}; border-radius: 8px;
            margin-top: 10px; padding-top: 8px; font-weight: 600;
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
        QListWidget#navigation {{ border: none; background: transparent; outline: none; }}
        QListWidget#navigation::item {{ color: {muted}; border-radius: 7px; padding: 0 12px; }}
        QListWidget#navigation::item:hover {{ background: {hover}; color: {text}; }}
        QListWidget#navigation::item:selected {{ background: #365fbe; color: white; }}
        QListWidget#navigation::item:focus {{ border: 1px solid {text}; }}
        QTabWidget::pane {{ border: none; }}
        QTabBar::tab {{
            background: transparent; color: {muted}; padding: 10px 14px;
            border-bottom: 2px solid {border};
        }}
        QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid #527de0; }}
        QTabBar::tab:focus {{ background: {hover}; }}
        QPushButton {{
            color: {text}; background: {panel}; border: 1px solid {border};
            border-radius: 5px; padding: 6px 12px; min-height: 18px;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed, QPushButton:checked {{ background: {hover}; border-color: #527de0; }}
        QPushButton:focus {{ border-color: #527de0; }}
        QPushButton[primary="true"] {{ background: #365fbe; color: white; border-color: #365fbe; }}
        QPushButton:disabled {{ color: {muted}; background: {background}; border-color: {border}; }}
        QLineEdit, QComboBox, QSpinBox {{
            min-height: 24px; color: {text}; background: {panel};
            border: 1px solid {border}; border-radius: 4px; padding: 2px 6px;
            selection-background-color: #365fbe; selection-color: white;
        }}
        QCheckBox {{ min-height: 24px; padding: 2px 0; }}
        QLineEdit {{ placeholder-text-color: {muted}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #527de0; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {muted}; }}
        QComboBox {{ padding-right: 24px; }}
        QTabBar::tear, QTabBar::scroller {{ background: {background}; }}
        QTabWidget::tab-bar {{ left: 0; }}
        QListWidget#checks {{ border: none; background: transparent; outline: none; }}
        QListWidget#checks::item {{ padding: 7px 4px; border-bottom: 1px solid {border}; }}
        QListWidget#checks::item:hover {{ background: {hover}; }}
        QListWidget#checks::item:selected {{ background: {hover}; color: {text}; }}
        QListWidget#checks::item:focus {{ border: 1px solid #527de0; }}
        QTreeView, QTableView {{ border: 1px solid {border}; border-radius: 4px; }}
        QTreeView::item {{ padding: 3px 0; }}
        QTableWidget#detectedFilesTable {{
            background: {panel}; alternate-background-color: {hover}; color: {text};
            selection-background-color: #365fbe; selection-color: white;
        }}
        QTableWidget#detectedFilesTable QHeaderView::section {{
            background: {background}; color: {muted}; border: none;
            border-bottom: 1px solid {border}; padding: 8px; font-weight: 600;
        }}
        QTableWidget#detectedFilesTable QTableCornerButton::section {{ background: {background}; }}
    """)
    if sidebar is not None:
        for row, name in enumerate(_ICON_PATHS):
            sidebar.item(row).setIcon(navigation_icon(name, muted, root.devicePixelRatioF()))
