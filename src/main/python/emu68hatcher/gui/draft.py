"""Capture and restore the editable GUI state around a config load."""

from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)


def capture_draft(window):
    """Record widget values and storage state so a failed load can roll back."""
    values = []
    for owner in (
        window.kickstart_tab,
        window.emu68_tab,
        window.display_tab,
        window.display_tab.appearance_content,
        window.kickstart_tab.icons_group,
        window.network_tab,
        window.output_tab,
        window.partitions_tab.capacity_row,
    ):
        for widget in owner.findChildren(QWidget):
            if isinstance(widget, QComboBox):
                value = widget.currentData(), widget.currentText(), widget.currentIndex()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, QLineEdit):
                value = widget.text()
            elif isinstance(widget, QPlainTextEdit):
                value = widget.toPlainText()
            elif isinstance(widget, QAbstractButton) and widget.isCheckable():
                value = widget.isChecked()
            else:
                continue
            values.append((widget, value))
    storage = window.storage_controller
    return {
        "values": values,
        "directories": window.kickstart_tab.asset_panel.directories,
        "packages": dict(window.packages_tab._requests),
        "locales": {
            name: check.isChecked() for name, check in window.kickstart_tab._locale_checks.items()
        },
        "icon_set": window.kickstart_tab.get_icon_set(),
        "layout": storage.model.to_layout_draft(),
        "partition_ids": list(storage.model.partition_ids),
        "selected_id": storage.model.selected_id,
        "capacity_text": window.partitions_tab.size_combo.currentText(),
        "capacity_error": window.partitions_tab.capacity_error,
        "capacity_enabled": window.partitions_tab.size_combo.isEnabled(),
        "storage": {
            name: getattr(storage, name)
            for name in (
                "_ags_configured",
                "enabled",
                "source",
                "roles",
                "inventory",
                "inspection_error",
                "allocation_state",
                "migration_notice",
                "layout_message",
            )
        },
    }


def restore_draft(controller, draft):
    from shiboken6 import isValid

    window = controller.window
    for widget, value in draft["values"]:
        if not isValid(widget):
            continue
        if isinstance(widget, QComboBox):
            index = widget.findData(value[0]) if value[0] is not None else widget.findText(value[1])
            # a release default entry changes its text with the release
            widget.setCurrentIndex(index if index >= 0 else value[2])
        elif isinstance(widget, QSpinBox):
            widget.setValue(value)
        elif isinstance(widget, QLineEdit):
            widget.setText(value)
        elif isinstance(widget, QPlainTextEdit):
            widget.setPlainText(value)
        else:
            widget.setChecked(value)
    window.emu68_tab.set_emu68_version(window.emu68_tab.get_emu68_version())
    window.network_tab._update_net_visibility()
    window.output_tab._on_mode_changed()
    window.kickstart_tab.asset_panel.directories = draft["directories"]
    window.kickstart_tab.asset_panel.scan()
    window.kickstart_tab.set_icon_set(draft["icon_set"])
    window.packages_tab.set_kickstart_version(window.kickstart_tab.get_selected_version())
    window.packages_tab._requests = draft["packages"]
    for name, value in draft["locales"].items():
        if name in window.kickstart_tab._locale_checks:
            window.kickstart_tab._locale_checks[name].setChecked(value)
    window.storage_controller.cancel_inspection()
    for name, value in draft["storage"].items():
        setattr(window.storage_controller, name, value)
    window.partitions_tab.set_config(draft["layout"], update=False)
    window.storage_controller.model.partition_ids = draft["partition_ids"]
    window.storage_controller.model.selected_id = draft["selected_id"]
    window.partitions_tab.size_combo.setEditText(draft["capacity_text"])
    window.partitions_tab.capacity_error = draft["capacity_error"]
    window.partitions_tab.size_combo.setEnabled(draft["capacity_enabled"])
    window.storage_controller._schedule()
    window.storage_controller.set_output(window.output_tab.get_config())
    controller._apply_context()
    window.packages_tab.refresh_packages()
