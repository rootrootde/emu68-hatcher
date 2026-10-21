"""Shared form layout helpers for the settings tabs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QLabel, QWidget


def form_layout(parent: QWidget, *, top: bool = False) -> QFormLayout:
    form = QFormLayout(parent)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if top:
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    return form


def set_widget_help(widget: QWidget, help_text: str) -> None:
    """set tooltip and accessible description on the widget and children that have none"""
    for item in (widget, *widget.findChildren(QWidget)):
        if not item.toolTip():
            item.setToolTip(help_text)
        if not item.accessibleDescription():
            item.setAccessibleDescription(help_text)


def add_form_row(
    form: QFormLayout,
    text: str,
    field: QWidget,
    help_text: str | None = None,
    labels: list[QLabel] | None = None,
) -> None:
    """add a labelled row; labels sharing one list get the width of the widest"""
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if help_text:
        label.setToolTip(help_text)
        label.setAccessibleDescription(help_text)
        set_widget_help(field, help_text)
    if labels is not None:
        labels.append(label)
        width = max(item.sizeHint().width() for item in labels)
        for item in labels:
            item.setFixedWidth(width)
    form.addRow(label, field)
