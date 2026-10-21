"""Coordinate view adapters, validated snapshots and side-effect-free GUI preflight."""

from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, QTimer, Signal

from emu68hatcher.config.schema import CURRENT_CONFIG_VERSION, BuildConfig
from emu68hatcher.data.package_selection import resolve_choices
from emu68hatcher.gui.draft import capture_draft, restore_draft
from emu68hatcher.gui.preflight import run_preflight


class ConfigurationController(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.changed.emit)

    def invalidate(self, *_args):
        if not self.window._loading_config:
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
        self._apply_context()
        self.invalidate()

    def _apply_context(self):
        window = self.window
        window.packages_tab.set_context(
            window.network_tab.get_network_stack(),
            ags_enabled=window.storage_controller.enabled,
            rgb2rtg_enabled=window.emu68_tab.get_rgb2rtg().enabled,
        )

    def resolution(self):
        """Resolve the same package entries collect_config hands to the build."""
        window = self.window
        entries = (
            window.packages_tab.get_config()
            + window.network_tab.extra_package_entries()
            + window.kickstart_tab.get_locale_entries()
        )
        return resolve_choices(
            {entry["name"].lower(): entry["enabled"] for entry in entries},
            window.kickstart_tab.get_selected_version(),
            window.emu68_tab.get_emu68_version().value,
            window.network_tab.get_network_stack(),
            ags_enabled=window.storage_controller.enabled,
            rgb2rtg_enabled=window.emu68_tab.get_rgb2rtg().enabled,
        )

    def load(self, config):
        window = self.window
        previous = capture_draft(window)
        window._loading_config = True
        try:
            with QSignalBlocker(window.storage_controller):
                try:
                    self._apply_config(config)
                except Exception:
                    restore_draft(self, previous)
                    raise
        finally:
            window._loading_config = False
            window.storage_controller.layout_changed.emit()
            window.storage_controller.changed.emit()
            self.refresh_context()
            window._sync_rgb2rtg_display()
        self.invalidate()

    def _apply_config(self, config):
        window = self.window
        window.storage_controller.cancel_inspection()
        window.kickstart_tab.set_config(
            config.kickstart, asset_directories=list(config.asset_directories)
        )
        window.display_tab.set_config(config.display)
        window.display_tab.set_picasso96_archive(config.display.picasso96_archive)
        window.emu68_tab.set_emu68_version(config.emu68_version)
        window.emu68_tab.set_settings(config.emu68_boot)
        window.emu68_tab.set_rgb2rtg(config.rgb2rtg)
        window.display_tab.set_emu68_boot_settings(config.emu68_boot)
        window.packages_tab.set_kickstart_version(config.kickstart.version.value)
        window.kickstart_tab.set_icon_set(config.icon_set)
        window.network_tab.set_network_stack(config.network_stack)
        window.network_tab.set_wifi_config(config.wifi)
        window.network_tab.set_roadshow_archive(config.roadshow_archive)
        window.network_tab.set_miamidx_key_directory(config.miamidx_key_directory)
        window.network_tab.set_network_settings(config.network)
        window.packages_tab.set_config(config.packages)
        window.kickstart_tab.set_locale(config.packages)
        window.output_tab.set_config(config.output)
        window.partitions_tab.set_config(config.partitions, update=False)
        window.storage_controller.load_ags(config.ags_import)
        window.storage_controller.set_output(window.output_tab.get_config())

    def collect_config(self):
        """Read adapters without editing the draft; validate at save/build boundaries."""
        window = self.window
        ks = window.kickstart_tab.get_config()
        disp = window.display_tab.get_config()
        hdmi_mode = disp.get("hdmi_mode", "1280*720-50")
        custom = (
            {
                key: disp[key]
                for key in (
                    "width",
                    "height",
                    "framerate",
                    "aspect_ratio",
                    "margins",
                    "interlace",
                    "reduced_blanking",
                )
            }
            if hdmi_mode == "Custom"
            else None
        )
        out = window.storage_controller.output
        wifi = window.network_tab.get_wifi_config()
        network = window.network_tab.get_network_settings()
        data = {
            "version": CURRENT_CONFIG_VERSION,
            "kickstart": {"version": ks["version"]},
            "asset_directories": [
                Path(p) for p in ks.get("asset_directories", []) if str(p).strip()
            ],
            "display": {
                "hdmi_mode": hdmi_mode,
                "custom": custom,
                "workbench_mode": disp["workbench_mode"],
                "picasso96_archive": window.display_tab.get_picasso96_archive(),
            },
            "packages": window.packages_tab.get_config()
            + window.network_tab.extra_package_entries()
            + window.kickstart_tab.get_locale_entries(),
            "icon_set": window.kickstart_tab.get_icon_set(),
            "partitions": window.partitions_tab.get_config().model_dump(mode="python"),
            "ags_import": window.storage_controller.ags_config(),
            "output": out if out.get("path") else None,
            "network_stack": window.network_tab.get_network_stack(),
            "roadshow_archive": window.network_tab.get_roadshow_archive(),
            "miamidx_key_directory": window.network_tab.get_miamidx_key_directory(),
            "wifi": wifi.model_dump(mode="python") if wifi else None,
            "network": network.model_dump(mode="python"),
            "rgb2rtg": window.emu68_tab.get_rgb2rtg().model_dump(mode="python"),
            "emu68_version": window.emu68_tab.get_emu68_version(),
            "emu68_boot": window._collect_emu68_boot_settings().model_dump(mode="python"),
        }
        return BuildConfig.model_validate(data)

    def snapshot(self):
        return self.collect_config().model_copy(deep=True)

    def preflight(self):
        return run_preflight(self)

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
