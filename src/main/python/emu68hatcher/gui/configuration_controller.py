"""Coordinate view adapters, validated snapshots and side-effect-free GUI preflight."""

from dataclasses import dataclass

from pydantic import ValidationError
from PySide6.QtCore import QObject, QSignalBlocker, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from emu68hatcher.config.ags_layout import validate_ags_layout
from emu68hatcher.data.icon_sets import get_icon_set_extra_adf
from emu68hatcher.data.install_media import get_required_install_media
from emu68hatcher.data.package_loader import get_adf_rules_for_version
from emu68hatcher.utils.host_tools import find_7z, find_hst_imager


@dataclass(frozen=True)
class ConfigurationIssue:
    message: str
    page: str
    subpage: str = ""
    field: str = ""
    severity: str = "error"


def validation_message(error):
    if isinstance(error, ValidationError):
        return "\n".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
    return str(error)


class ConfigurationController(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.last_validated = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.changed.emit)

    def invalidate(self, *_args):
        if not self.window._loading_config:
            self.last_validated = None
            self._timer.start()

    def set_os_version(self, version):
        self.window.packages_tab.set_kickstart_version(version)
        self.invalidate()

    def set_release(self, version):
        self.window.packages_tab.set_emu68_version(version)
        self.window.kickstart_tab.set_emu68_version(version)
        self.invalidate()

    def refresh_context(self):
        window = self.window
        if window._loading_config:
            return
        window.packages_tab.set_context(
            window.network_tab.get_network_stack(),
            window.display_tab.workbench_theme_combo.currentData(),
        )
        self.invalidate()

    def load(self, config):
        window = self.window
        # Validate before touching views, then retain an independent rollback snapshot.
        data = config.model_dump(mode="python")
        data["wifi"] = config.wifi
        config = type(config).model_validate(data)
        previous = self._capture_draft()
        window._loading_config = True
        try:
            with QSignalBlocker(window.storage_controller):
                try:
                    window._apply_config(config)
                except Exception:
                    self._restore_draft(previous)
                    raise
        finally:
            window._loading_config = False
            window.storage_controller.layout_changed.emit()
            window.storage_controller.changed.emit()
            self.refresh_context()
        window.config = config
        self.invalidate()

    def _capture_draft(self):
        window = self.window
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
                    value = widget.currentData(), widget.currentText()
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
                name: check.isChecked()
                for name, check in window.kickstart_tab._locale_checks.items()
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
                    "legacy_content_device",
                    "migration_notice",
                    "layout_message",
                )
            },
        }

    def _restore_draft(self, draft):
        from shiboken6 import isValid

        window = self.window
        for widget, value in draft["values"]:
            if not isValid(widget):
                continue
            if isinstance(widget, QComboBox):
                index = (
                    widget.findData(value[0]) if value[0] is not None else widget.findText(value[1])
                )
                widget.setCurrentIndex(index)
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
        window.packages_tab.set_context(
            window.network_tab.get_network_stack(),
            window.display_tab.workbench_theme_combo.currentData(),
        )

    def snapshot(self):
        config = self.window.collect_config().model_copy(deep=True)
        self.last_validated = config.model_copy(deep=True)
        return config

    def preflight(self):
        window = self.window
        storage = window.storage_controller
        issues = []

        def add(message, page, subpage="", field="", severity="error"):
            issues.append(ConfigurationIssue(message, page, subpage, field, severity))

        if storage.enabled and storage.inspection_pending():
            add(
                "Wait for the AGS source check to finish.",
                "storage",
                "ags",
                "source_edit",
                "pending",
            )
        elif storage.enabled and storage.inspection_error:
            add(
                f"AGS source is unreadable: {storage.inspection_error}",
                "storage",
                "ags",
                "source_edit",
            )
        elif storage.enabled and storage.allocation_state != "ready":
            add(storage.layout_message, "storage", "ags", "source_edit")
        if window.output_tab.needs_disk_target():
            add("Select an SD card.", "storage", "", "disk_combo")
        if not window.output_tab.get_config().get("path"):
            add("Select an output location.", "storage", "", "output_path")
        if window.partitions_tab.capacity_error:
            add(window.partitions_tab.capacity_error, "storage", "", "size_combo")
        for error in storage.model.errors:
            add(error, "storage", "partitions", "part_table")
        if window.partitions_tab.extra_content_scan_pending():
            add(
                "Extra-file folder sizes are still being checked.",
                "storage",
                "extras",
                "extras_partition_combo",
                "pending",
            )
        else:
            for error in window.partitions_tab.extra_content_errors():
                add(error, "storage", "extras", "extras_partition_combo")
        if (
            window.network_tab.wifi_ssid.text().strip()
            and window.network_tab.get_wifi_config() is None
        ):
            add(
                "Enter a WiFi password of 8–63 characters, or leave it empty for an open network.",
                "system",
                "network",
                "wifi_password",
            )
        try:
            window.network_tab.get_network_settings()
        except ValueError as error:
            add(validation_message(error), "system", "network", "eth_addr")
        panel = window.kickstart_tab.asset_panel
        if not panel.directories:
            add(
                "Add ROMs and installation media.",
                "system",
                "amiga",
                "dir_list",
            )
        elif not panel.scan_directories:
            add("None of the asset directories are available.", "system", "amiga", "dir_list")
        elif panel._active:
            add(
                "Wait for the ROM and media scans to finish.",
                "system",
                "amiga",
                "dir_list",
                "pending",
            )
        else:
            if not any(row[0] == "boot" for row in panel.results["roms"]):
                add(
                    "No matching boot ROM found. Rescan your asset directories.",
                    "system",
                    "amiga",
                    "dir_list",
                )
            found = {row[1] for row in panel.results["media"] if row[0] == "found"}
            required = set(get_required_install_media(window.kickstart_tab.get_selected_version()))
            missing = required - found
            if missing:
                add(
                    "Missing installation media: " + ", ".join(sorted(missing)),
                    "system",
                    "amiga",
                    "dir_list",
                )
            icon_adf = get_icon_set_extra_adf(
                window.kickstart_tab.get_icon_set(), window.kickstart_tab.get_selected_version()
            )
            if icon_adf and icon_adf not in found:
                add(
                    f"The selected icons require {icon_adf}. Add its media or choose another icon set.",
                    "appearance",
                    "appearance",
                    "icon_set_combo",
                )
            optional_missing = {
                rule.adf
                for rule in get_adf_rules_for_version(window.kickstart_tab.get_selected_version())
                if rule.package
                and not rule.mandatory
                and rule.package.lower() in window.packages_tab.resolution.selected
                and rule.adf not in required
                and rule.adf not in found
            }
            if optional_missing:
                add(
                    "Selected software requires media: " + ", ".join(sorted(optional_missing)),
                    "system",
                    "amiga",
                    "dir_list",
                )
        for label, finder in (("HST Imager", find_hst_imager), ("7-Zip", find_7z)):
            if not finder():
                add(f"Install {label}.", "overview", "tools")
        for label, path, page, section, field in (
            (
                "Roadshow archive",
                window.network_tab.get_roadshow_archive()
                if window.network_tab.radio_roadshow.isChecked()
                else None,
                "system",
                "network",
                "roadshow_archive_edit",
            ),
            (
                "MiamiDX keys",
                window.network_tab.get_miamidx_key_directory()
                if window.network_tab.radio_miamidx.isChecked()
                else None,
                "system",
                "network",
                "miamidx_key_directory_edit",
            ),
            (
                "Picasso96 archive",
                window.display_tab.get_picasso96_archive(),
                "appearance",
                "display",
                "picasso96_archive_edit",
            ),
        ):
            if path is not None and not path.exists():
                add(f"{label} was not found: {path}", page, section, field)
        resolution = window.packages_tab.resolution
        for token, names in resolution.unsatisfiable.items():
            add(f"{token}: required by {', '.join(names)}", "software")
        for tab in (window.kickstart_tab, window.packages_tab):
            notice = getattr(tab, "catalog_notice", None)
            if notice and notice.text():
                add(notice.text(), "software", severity="warning")
        if any(issue.severity != "warning" for issue in issues):
            return None, issues
        try:
            config = self.snapshot()
        except ValueError as error:
            message = validation_message(error)
            if "C790" in message or "Native-video capture" in message:
                add(message, "appearance", "display", "unicam_device_combo")
            elif "output" in message:
                add(message, "storage", "", "output_path")
            elif "AGS" in message or "source_image" in message:
                add(message, "storage", "ags", "source_edit")
            else:
                add(message, "system", "hardware")
            return None, issues
        for error in (
            validate_ags_layout(config) if config.ags_import and config.ags_import.enabled else ()
        ):
            add(error, "storage", "ags", "source_edit")
        return (None if any(issue.severity != "warning" for issue in issues) else config), issues

    def summary(self):
        window = self.window
        output = window.output_tab.get_config()
        stack = window.network_tab.get_network_stack()
        storage = window.storage_controller
        partition_count = len(storage.model.partitions)
        return [
            (
                "System",
                f"AmigaOS {window.kickstart_tab.get_selected_version()} · Emu68 {window.emu68_tab.get_emu68_version().value}",
            ),
            (
                "Software",
                f"{len(window.packages_tab.resolution.selected)} packages · {stack.value if stack else 'No network'}",
            ),
            ("Display", window.display_tab.hdmi_mode_combo.currentText()),
            ("Icons", window.kickstart_tab.icon_set_combo.currentText()),
            (
                "Storage",
                f"{storage.model.disk_size / 1024**3:.2f} GiB · {partition_count} partition{'s' if partition_count != 1 else ''}"
                + (" · AGS" if storage.enabled else ""),
            ),
            (
                "Output",
                (output.get("path") or "Not selected")
                + (f" → {output['flash_target']}" if output.get("flash_target") else ""),
            ),
        ]
