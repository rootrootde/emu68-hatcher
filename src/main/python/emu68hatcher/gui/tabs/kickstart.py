"""Kickstart/Workbench version + Amiga asset directories tab"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.schema import (
    SUPPORTED_KICKSTARTS,
    InstallMediaConfig,
    KickstartConfig,
    KickstartVersion,
)
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.widgets.asset_scan_panel import AssetScanPanel

# dropdown order follows schema.SUPPORTED_KICKSTARTS (add a version there to expose it here)
_SELECTABLE_VERSIONS: tuple[str, ...] = tuple(v.value for v in SUPPORTED_KICKSTARTS)
_DEFAULT_VERSION: str = KickstartVersion.V3_2_3.value


class KickstartTab(QWidget):
    """workbench version + multi-directory asset configuration"""

    # signal emitted when WB version changes
    version_changed = Signal(str)
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.icon_sets: list[dict] = []
        self._recognized_adfs: set[str] = set()
        self._requested_icon_set: str | None = None
        self._locale_checks: dict[str, QCheckBox] = {}
        self._emu68_version = None
        self.setup_ui()

    def setup_ui(self):
        # scroll wrapper keeps the language grid reachable on short windows
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout = page_layout(content)

        #####################
        # workbench Version #
        #####################
        version_group = QGroupBox("AmigaOS version")
        version_group_layout = QVBoxLayout(version_group)

        version_layout = QHBoxLayout()
        version_layout.addWidget(QLabel("Version:"))
        self.version_combo = QComboBox()
        self.version_combo.addItems(_SELECTABLE_VERSIONS)
        self.version_combo.setCurrentIndex(_SELECTABLE_VERSIONS.index(_DEFAULT_VERSION))
        self.version_combo.currentIndexChanged.connect(self.on_version_changed)
        version_layout.addWidget(self.version_combo)
        version_layout.addStretch()
        version_group_layout.addLayout(version_layout)

        # The selector is embedded in Appearance; version/media filtering stays here.
        self._load_icon_sets(self.get_selected_version())
        self.icons_group = QGroupBox("Icons")
        icon_layout = QHBoxLayout(self.icons_group)
        icon_layout.addWidget(QLabel("Icons:"))
        self.icon_set_combo = QComboBox()
        self.icon_set_combo.setMinimumWidth(200)
        self.icon_set_combo.setToolTip("GlowIcons recommended for high color displays")
        self.icon_set_combo.activated.connect(self._on_icon_set_activated)
        self.icon_set_combo.currentIndexChanged.connect(lambda _index: self.settings_changed.emit())
        self._populate_icon_set_combo()
        icon_layout.addWidget(self.icon_set_combo)
        icon_layout.addStretch()

        layout.addWidget(version_group)

        self.asset_panel = AssetScanPanel(self.get_selected_version())
        self.dir_list = self.asset_panel.dir_list
        self.rom_status = self.asset_panel.rom_status
        self.whdload_status = self.asset_panel.whdload_status
        self.adf_status = self.asset_panel.adf_status
        self.asset_panel.adf_results.connect(self._on_adf_results)
        layout.addWidget(self.asset_panel)

        #############
        # languages #
        #############
        # locale disks vary by Workbench version, so they live here next to the version picker
        self._lang_group = QGroupBox("Languages")
        self._lang_grid = QGridLayout(self._lang_group)
        self._build_language_grid()
        layout.addWidget(self._lang_group)

        layout.addStretch()

    #####################
    # directory Methods #
    #####################

    def on_version_changed(self):
        """re-scan + refresh the icon-set list and language grid when version changes"""
        self.version_changed.emit(self.get_selected_version())
        if hasattr(self, "icon_set_combo"):
            self._recognized_adfs.clear()
            self._requested_icon_set = None
            self._load_icon_sets(self.get_selected_version())
            self._populate_icon_set_combo()
        if hasattr(self, "_lang_grid"):
            self._build_language_grid()
        if hasattr(self, "asset_panel"):
            self.asset_panel.set_version(self.get_selected_version())

    def _build_language_grid(self, columns: int = 3):
        """(re)build the locale checkbox grid for the selected Workbench version"""
        from emu68hatcher.data.package_loader import get_packages_for_version

        self._locale_checks.clear()
        while self._lang_grid.count():
            item = self._lang_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        locales = [
            p
            for p in get_packages_for_version(self.get_selected_version(), self._emu68_version)
            if p.group == "Locale"
        ]
        for i, p in enumerate(sorted(locales, key=lambda p: p.friendly_name or p.name)):
            # "German (DE) Locale Files" -> "German (DE)"; the 3.1 bundle stays "Locale Files"
            label = (p.friendly_name or p.name).replace(" Locale Files", "").strip()
            cb = QCheckBox(label or (p.friendly_name or p.name))
            cb.setChecked(p.default)
            cb.toggled.connect(lambda _checked: self.settings_changed.emit())
            self._lang_grid.addWidget(cb, i // columns, i % columns)
            self._locale_checks[p.name] = cb
        self._lang_group.setVisible(bool(locales))

    def set_emu68_version(self, version):
        self._emu68_version = version
        self.refresh_catalog()

    def refresh_catalog(self):
        previous = {name: checkbox.isChecked() for name, checkbox in self._locale_checks.items()}
        self._build_language_grid()
        for name, checkbox in self._locale_checks.items():
            if name in previous:
                checkbox.setChecked(previous[name])
        return sorted(
            name
            for name, enabled in previous.items()
            if enabled and name not in self._locale_checks
        )

    def get_locale_entries(self) -> list[dict]:
        """selected locale packages as flat {name, enabled} entries for config.packages"""
        return [{"name": n, "enabled": cb.isChecked()} for n, cb in self._locale_checks.items()]

    def set_locale(self, packages):
        """restore locale checkboxes from a loaded config"""
        enabled = {p.name: p.enabled for p in packages}
        for name, cb in self._locale_checks.items():
            if name in enabled:
                cb.setChecked(enabled[name])

    def _load_icon_sets(self, ks_version: str):
        """load the icon sets available for a Kickstart version"""
        self.icon_sets = []
        try:
            from emu68hatcher.data.icon_sets import (
                get_icon_set_extra_adf,
                get_icon_sets_for_version,
            )

            for r in get_icon_sets_for_version(ks_version):
                name = r.get("name", "Standard")
                self.icon_sets.append(
                    {
                        "name": name,
                        "description": r.get("description", ""),
                        "default": r.get("default", False),
                        "required_adf": get_icon_set_extra_adf(name, ks_version),
                    }
                )
        except Exception:
            pass

        if not self.icon_sets:
            self.icon_sets = [
                {"name": "Standard", "description": "Standard Icon set", "default": True}
            ]
            if ks_version.startswith("3.2"):
                self.icon_sets.append(
                    {
                        "name": "GlowIcons",
                        "description": "Glow Icons for high color modes",
                        "default": True,
                        "required_adf": "GlowIcons3_2",
                    }
                )
                self.icon_sets[0]["default"] = False

    def _populate_icon_set_combo(self):
        """fill the icon set dropdown from self.icon_sets, picking the default"""
        from emu68hatcher.data.icon_sets import format_adf_name

        self.icon_set_combo.clear()
        available_default = None
        first_available = None
        requested_idx = None
        for i, icon_set in enumerate(self.icon_sets):
            name = icon_set["name"]
            required_adf = icon_set.get("required_adf")
            available = required_adf is None or required_adf in self._recognized_adfs
            label = name if available else f"{name} (ADF not found)"
            self.icon_set_combo.addItem(label, name)
            item = self.icon_set_combo.model().item(i)
            item.setEnabled(available)
            description = icon_set["description"]
            if not available:
                description += f"\nRequires a recognized {format_adf_name(required_adf)} ADF."
            item.setToolTip(description)
            if available and first_available is None:
                first_available = i
            if available and icon_set["default"]:
                available_default = i
            if name == self._requested_icon_set:
                requested_idx = i
        target = requested_idx
        if target is None:
            target = available_default if available_default is not None else first_available
        self.icon_set_combo.setCurrentIndex(target if target is not None else -1)

    def _on_adf_results(self, found_media: list, _truncated: bool) -> None:
        self._recognized_adfs = {media.adf_name for media in found_media}
        self._populate_icon_set_combo()

    def _on_icon_set_activated(self, index: int) -> None:
        self._requested_icon_set = self.icon_set_combo.itemData(index)

    def get_icon_set(self) -> str:
        """selected icon set name"""
        return self.icon_set_combo.currentData() or "Standard"

    def set_icon_set(self, icon_set_name: str):
        """set the icon set dropdown to a specific value"""
        self._requested_icon_set = icon_set_name
        self._populate_icon_set_combo()

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        return self.asset_panel.shutdown_workers(timeout_ms)

    def get_selected_version(self) -> str:
        """get the selected version from the dropdown"""
        idx = self.version_combo.currentIndex()
        if 0 <= idx < len(_SELECTABLE_VERSIONS):
            return _SELECTABLE_VERSIONS[idx]
        return _DEFAULT_VERSION

    def get_config(self) -> dict:
        return {
            "version": self.get_selected_version(),
            "asset_directories": [
                self.dir_list.item(i).text() for i in range(self.dir_list.count())
            ],
        }

    def set_config(
        self,
        ks_config: KickstartConfig,
        media_config: InstallMediaConfig,
        asset_directories: list[Path] | None = None,
    ):
        """populate the tab from config objects"""
        # kickstart.version drives the Workbench dropdown; 3.9 maps to 3.1 (same ROM, hidden in UI)
        version_to_set = ks_config.version.value
        if version_to_set not in _SELECTABLE_VERSIONS:
            version_to_set = KickstartVersion.V3_1.value
        idx = _SELECTABLE_VERSIONS.index(version_to_set)
        self.version_combo.setCurrentIndex(idx)

        # asset_directories wins; fall back to merging legacy single-dir fields for round-trips
        dirs: list[Path | str] = []
        if asset_directories:
            dirs.extend(asset_directories)
        else:
            if ks_config.rom_directory:
                dirs.append(ks_config.rom_directory)
            if media_config.directory and media_config.directory != ks_config.rom_directory:
                dirs.append(media_config.directory)
        self.asset_panel.directories = dirs
        self.asset_panel.scan()
