"""custom Qt widgets reused across tabs"""

from PySide6.QtWidgets import QComboBox


def select_combo_by_data(combo: QComboBox, value) -> None:
    """pick the combo item whose data() == value; no-op if not found"""
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            combo.setCurrentIndex(i)
            return


def set_release_default(combo: QComboBox, value, label: str = "") -> None:
    """label item 0 (the follow-the-release entry) after value and hide the item it duplicates"""
    model = combo.model()
    for i in range(1, combo.count()):
        duplicate = combo.itemData(i) == value
        if duplicate:
            label = combo.itemText(i)
        combo.view().setRowHidden(i, duplicate)
        model.item(i).setEnabled(not duplicate)
    combo.setItemText(0, f"{label} (default)")
    if not model.item(combo.currentIndex()).isEnabled():
        combo.setCurrentIndex(0)


def select_release_option(combo: QComboBox, value) -> None:
    """select_combo_by_data, mapping a hidden duplicate of the release default to item 0"""
    select_combo_by_data(combo, value)
    if not combo.model().item(combo.currentIndex()).isEnabled():
        combo.setCurrentIndex(0)
