"""custom Qt widgets reused across tabs"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox


def set_combo_item_enabled(combo: QComboBox, row: int, enabled: bool) -> None:
    """enable or grey out one combo item"""
    # not model().item(row): pyside 6.8 keeps that wrapper registered after clear() deleted the
    # item, and an event allocated at the same address then reaches python as a QStandardItem
    model = combo.model()
    index = model.index(row, 0)
    flags = model.flags(index)
    flags = flags | Qt.ItemFlag.ItemIsEnabled if enabled else flags & ~Qt.ItemFlag.ItemIsEnabled
    # QStandardItem keeps its flags under this role
    model.setData(index, flags.value, Qt.ItemDataRole.UserRole - 1)


def select_combo_by_data(combo: QComboBox, value) -> None:
    """pick the combo item whose data() == value; no-op if not found"""
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def set_release_default(combo: QComboBox, value, label: str = "") -> None:
    """label item 0 (the follow-the-release entry) after value and hide the item it duplicates"""
    for i in range(1, combo.count()):
        duplicate = combo.itemData(i) == value
        if duplicate:
            label = combo.itemText(i)
        combo.view().setRowHidden(i, duplicate)
        set_combo_item_enabled(combo, i, not duplicate)
    # an explicit choice stays selected while hidden, so switching release back keeps it
    combo.setItemText(0, f"{label} (default)")
