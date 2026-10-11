"""Main window with six freely accessible work areas."""

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher import __version__
from emu68hatcher.builder.staging.scripts.generator import render_boot_partition_files
from emu68hatcher.config.boot_models import Emu68BootSettings
from emu68hatcher.config.defaults import create_default_config
from emu68hatcher.config.display_models import CustomScreenMode
from emu68hatcher.config.loader import load_config, save_config
from emu68hatcher.config.schema import CURRENT_CONFIG_VERSION, BuildConfig, OutputType
from emu68hatcher.data.rom_detection import identify_kickstart
from emu68hatcher.gui.configuration_controller import ConfigurationController, validation_message
from emu68hatcher.gui.design import apply_design, style_sections
from emu68hatcher.gui.dialogs import BuildProgressDialog
from emu68hatcher.gui.storage_controller import StorageController
from emu68hatcher.gui.tabs import (
    DisplayTab,
    Emu68Tab,
    KickstartTab,
    NetworkTab,
    OutputTab,
    PackagesTab,
    PartitionsTab,
    StartTab,
)
from emu68hatcher.gui.tabs.ags_tab import AGSTab
from emu68hatcher.gui.tabs.start import _find_app_icon, _render_icon
from emu68hatcher.gui.work_areas import (
    PAGE_DESCRIPTIONS,
    PAGE_LABELS,
    OverviewPage,
    SectionPage,
    StoragePage,
    SummaryPage,
    scroll_page,
    scroll_to,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = create_default_config()
        self._loading_config = False
        self._build_active = False
        self.controller = ConfigurationController(self)
        self.storage_controller = StorageController(self)
        self.setup_ui()
        QApplication.instance().paletteChanged.connect(self._refresh_design)
        self.resize(1200, 800)
        QTimer.singleShot(0, self.start_tab.check_for_updates)

    def setup_ui(self):
        self.setWindowTitle(f"Emu68 Hatcher {__version__}")
        self.setMinimumSize(800, 600)
        central = QWidget()
        central.setObjectName("workspace")
        self.setCentralWidget(central)
        body = QHBoxLayout(central)
        body.setContentsMargins(12, 12, 12, 0)
        body.setSpacing(0)
        rail = QWidget()
        rail.setFixedWidth(196)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 12, 0, 0)
        rail_layout.setSpacing(24)
        brand = QHBoxLayout()
        brand.setContentsMargins(12, 0, 0, 0)
        icon_path = _find_app_icon()
        if icon_path:
            logo = QLabel()
            logo.setPixmap(_render_icon(icon_path, 28))
            brand.addWidget(logo)
        brand_label = QLabel("Emu68 Hatcher")
        brand_label.setProperty("heading", "brand")
        brand.addWidget(brand_label)
        brand.addStretch()
        rail_layout.addLayout(brand)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("navigation")
        self.sidebar.setIconSize(QSize(22, 22))
        self.sidebar.setSpacing(4)
        font = self.sidebar.font()
        font.setPointSize(13)
        self.sidebar.setFont(font)
        self.sidebar.setAccessibleName("Work areas")
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rail_layout.addWidget(self.sidebar, 1)
        version = QLabel(f"Version {__version__}")
        version.setProperty("tone", "muted")
        version.setContentsMargins(12, 0, 0, 12)
        rail_layout.addWidget(version)
        body.addWidget(rail)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 12, 12, 12)
        layout.setSpacing(20)
        header = QVBoxLayout()
        header.setSpacing(6)
        self.page_title = QLabel()
        self.page_title.setProperty("heading", "page")
        self.page_description = QLabel()
        self.page_description.setProperty("tone", "muted")
        self.page_description.setWordWrap(True)
        header.addWidget(self.page_title)
        header.addWidget(self.page_description)
        layout.addLayout(header)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        body.addWidget(content, 1)

        self.start_tab = StartTab()
        self.kickstart_tab = KickstartTab()
        self.emu68_tab = Emu68Tab()
        self.display_tab = DisplayTab()
        self.network_tab = NetworkTab()
        self.packages_tab = PackagesTab(
            kickstart_version=self.kickstart_tab.get_selected_version(),
            emu68_version=self.emu68_tab.get_emu68_version().value,
        )
        self.output_tab = OutputTab()
        self.partitions_tab = PartitionsTab(controller=self.storage_controller)
        self.ags_tab = AGSTab(controller=self.storage_controller)
        self.overview = SummaryPage()
        self.overview.summary_group.hide()
        self.review = SummaryPage()
        self.overview.destination_requested.connect(self.navigate)
        self.review.destination_requested.connect(self.navigate)
        self.display_tab.appearance_content.layout().insertWidget(1, self.kickstart_tab.icons_group)
        self.storage_page = StoragePage(self.output_tab, self.partitions_tab, self.ags_tab)
        self.pages = {
            "overview": OverviewPage(self.overview, self.start_tab),
            "system": SectionPage(
                [
                    ("hardware", "Hardware & Emu68", self.emu68_tab),
                    ("amiga", "AmigaOS & files", self.kickstart_tab),
                    ("network", "Network", self.network_tab),
                ]
            ),
            "appearance": self.display_tab,
            "software": self.packages_tab,
            "storage": self.storage_page,
            "review": self.review,
        }
        self.page_widgets = {}
        for key, page in self.pages.items():
            item = QListWidgetItem(PAGE_LABELS[key])
            item.setSizeHint(QSize(0, 48))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.sidebar.addItem(item)
            widget = scroll_page(page) if key in {"overview", "storage", "review"} else page
            self.page_widgets[key] = widget
            self.stack.addWidget(widget)
        self.sidebar.currentRowChanged.connect(
            lambda row: (
                self.navigate(self.sidebar.item(row).data(Qt.ItemDataRole.UserRole))
                if row >= 0
                else None
            )
        )

        menu = self.menuBar().addMenu("File")
        for text, shortcut, callback in (
            ("Load Configuration…", QKeySequence.StandardKey.Open, self.open_config),
            ("Save Configuration…", QKeySequence.StandardKey.Save, self.save_config_file),
        ):
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.build_btn = QPushButton("Build image")
        self.build_btn.clicked.connect(self.build_image)
        self.build_btn.setProperty("primary", True)
        preview_btn = QPushButton("Preview boot files…")
        preview_btn.clicked.connect(self.show_boot_preview)
        review_actions = QHBoxLayout()
        review_actions.addWidget(preview_btn)
        review_actions.addStretch()
        review_actions.addWidget(self.build_btn)
        self.review.content_layout.addLayout(review_actions)
        self.review.content_layout.addStretch()
        self.preview_dialog = QDialog(self)
        self.preview_dialog.setWindowTitle("Generated boot files")
        self.preview_dialog.resize(800, 650)
        QVBoxLayout(self.preview_dialog).addWidget(self.emu68_tab.preview_pane)
        self.emu68_tab.preview_pane.show()
        self.review_btn = QPushButton("Review && build")
        self.review_btn.setProperty("primary", True)
        self.review_btn.clicked.connect(lambda: self.navigate("review"))
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.review_btn)
        layout.addLayout(footer)

        self.start_tab.catalog_changed.connect(self._refresh_catalog)
        self.start_tab.status_changed.connect(self.controller.invalidate)
        self.kickstart_tab.version_changed.connect(self.controller.set_os_version)
        self.emu68_tab.emu68_version_changed.connect(self.controller.set_release)
        self.kickstart_tab.set_emu68_version(self.emu68_tab.get_emu68_version())
        for radio in (
            self.network_tab.radio_none,
            self.network_tab.radio_roadshow,
            self.network_tab.radio_amitcp_ng,
            self.network_tab.radio_miamidx,
        ):
            radio.toggled.connect(self.controller.refresh_context)
        self.display_tab.workbench_theme_combo.currentIndexChanged.connect(
            self.controller.refresh_context
        )
        self.controller.refresh_context()
        self.output_tab.target_size_changed.connect(self.partitions_tab.set_auto_disk_size)
        self.output_tab.target_size_cleared.connect(self._target_size_cleared)
        self.storage_controller.changed.connect(self.controller.invalidate)
        self.emu68_tab.settings_changed.connect(self.controller.invalidate)
        self.display_tab.settings_changed.connect(self.controller.invalidate)
        self.packages_tab.selection_changed.connect(self.controller.invalidate)
        self.kickstart_tab.settings_changed.connect(self.controller.invalidate)
        self.kickstart_tab.asset_panel.state_changed.connect(self.controller.invalidate)
        self.kickstart_tab.asset_panel.rom_results.connect(self.controller.invalidate)
        self.kickstart_tab.asset_panel.adf_results.connect(self.controller.invalidate)
        # Adapters keep incomplete input in the existing controls; every edit invalidates review.
        for widget in central.findChildren(QWidget):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.controller.invalidate)
            elif isinstance(widget, QPlainTextEdit) and not widget.isReadOnly():
                widget.textChanged.connect(self.controller.invalidate)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.controller.invalidate)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self.controller.invalidate)
            elif isinstance(widget, QAbstractButton) and widget.isCheckable():
                widget.toggled.connect(self.controller.invalidate)
        self.output_tab.output_path.textChanged.connect(self._output_changed)
        self.output_tab.disk_combo.currentIndexChanged.connect(self._output_changed)
        self.output_tab.mode_buttons.buttonClicked.connect(self._output_changed)
        self.output_tab.sparse_cb.toggled.connect(self._output_changed)
        self.output_tab.verify_after_flash_cb.toggled.connect(self._output_changed)
        self._output_changed()
        self.controller.changed.connect(self.refresh_review)
        style_sections(central)
        apply_design(central, self.sidebar)
        self.navigate("overview")
        self.statusBar().showMessage("Ready")

    def _target_size_cleared(self):
        if self.output_tab.mode_img.isChecked():
            self.partitions_tab.clear_auto_disk_size()
        else:
            self.partitions_tab.size_combo.setEnabled(False)
            self.partitions_tab.reset_btn.setEnabled(False)
            self.partitions_tab.auto_size_label.setText(
                "Select an SD card to read its actual capacity."
            )
            self.partitions_tab.auto_size_label.show()
        self.controller.invalidate()

    def _output_changed(self, *_args):
        if not self._loading_config:
            self.storage_controller.set_output(self.output_tab.get_config())

    def navigate(self, page, subpage="", field=""):
        if page not in self.pages:
            raise ValueError(f"Unknown work area: {page}")
        self.stack.setCurrentWidget(self.page_widgets[page])
        row = list(PAGE_LABELS).index(page)
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(row)
        self.sidebar.blockSignals(False)
        self.page_title.setText(PAGE_LABELS[page])
        self.page_description.setText(PAGE_DESCRIPTIONS[page])
        target = self.pages[page]
        if subpage and hasattr(target, "navigate"):
            target.navigate(subpage)
        self.review_btn.setVisible(page != "review")
        if field:
            for owner in (
                self.output_tab,
                self.partitions_tab,
                self.ags_tab,
                self.kickstart_tab,
                self.emu68_tab,
                self.display_tab,
                self.network_tab,
            ):
                widget = getattr(owner, field, None)
                if isinstance(widget, QWidget):
                    widget.setFocus()
                    scroll_to(widget)
                    break
        if page in {"review", "overview"}:
            self.refresh_review()

    def refresh_review(self):
        if self._loading_config or self._build_active:
            return
        config, issues = self.controller.preflight()
        self.review.show_summary(self.controller.summary())
        for page in (self.overview, self.review):
            page.show_issues(issues)
        self.build_btn.setEnabled(config is not None)
        output = self.output_tab.get_config()
        self.build_btn.setText(
            "Write SD card"
            if output["type"] == OutputType.DEVICE.value
            else "Build image && write SD card"
            if output.get("flash_target")
            else "Build image"
        )
        for row, key in enumerate(PAGE_LABELS):
            relevant = [issue for issue in issues if issue.page == key]
            state = (
                "checking"
                if any(issue.severity == "pending" for issue in relevant)
                else (
                    "incomplete"
                    if any(issue.severity == "error" for issue in relevant)
                    else "warning"
                    if relevant
                    else ""
                )
            )
            self.sidebar.item(row).setToolTip(PAGE_LABELS[key] + (f" · {state}" if state else ""))
        if self.preview_dialog.isVisible():
            self._refresh_boot_files_preview()

    def _refresh_design(self, _palette):
        apply_design(self.centralWidget(), self.sidebar)

    def closeEvent(self, event):
        running = []
        for label, owner in (
            ("asset scans", self.kickstart_tab),
            ("disk scan", self.output_tab),
            ("extra content scan", self.partitions_tab),
            ("AGS source check", self.storage_controller),
            ("downloads", self.start_tab),
        ):
            if not owner.shutdown_workers():
                running.append(label)
        if running:
            self.statusBar().showMessage("Waiting for " + ", ".join(running))
            event.ignore()
            return
        super().closeEvent(event)

    def open_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Configuration", "", "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            self.controller.load(load_config(Path(path)))
            self.statusBar().showMessage(f"Loaded: {path}")
        except Exception as error:
            QMessageBox.critical(
                self, "Error", f"Failed to load config: {validation_message(error)}"
            )

    def _apply_config(self, config):
        self.storage_controller.cancel_inspection()
        self.kickstart_tab.set_config(
            config.kickstart, config.install_media, asset_directories=list(config.asset_directories)
        )
        self.display_tab.set_config(config.display)
        self.display_tab.set_picasso96_archive(config.display.picasso96_archive)
        self.emu68_tab.set_emu68_version(config.emu68_version)
        self.emu68_tab.set_settings(config.emu68_boot)
        self.display_tab.set_emu68_boot_settings(config.emu68_boot)
        self.packages_tab.set_kickstart_version(config.kickstart.version.value)
        self.kickstart_tab.set_icon_set(config.icon_set)
        self.network_tab.set_network_stack(config.network_stack)
        self.network_tab.set_wifi_config(config.wifi)
        self.network_tab.set_roadshow_archive(config.roadshow_archive)
        self.network_tab.set_miamidx_key_directory(config.miamidx_key_directory)
        self.network_tab.set_network_settings(config.network)
        self.packages_tab.set_config(config.packages)
        self.kickstart_tab.set_locale(config.packages)
        self.output_tab.set_config(config.output)
        self.partitions_tab.set_config(config.partitions, update=False)
        self.storage_controller.load_ags(config.ags_import)
        self.storage_controller.set_output(self.output_tab.get_config())

    def save_config_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Configuration", "emu68-config.json", "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            save_config(self.controller.snapshot(), Path(path))
            self.statusBar().showMessage(f"Saved: {path}")
        except Exception as error:
            QMessageBox.critical(
                self, "Error", f"Failed to save config: {validation_message(error)}"
            )

    def _current_boot_rom_filename(self):
        for status, _name, _version, _model, path in self.kickstart_tab.asset_panel.results["roms"]:
            if status == "boot":
                info = identify_kickstart(Path(path))
                return (info.get("fat32_name") or "kick.rom") if info else "kick.rom"
        return "kick.rom"

    def _render_boot_files_preview(self):
        display = self.display_tab.get_config()
        screen_mode = display.get("hdmi_mode") or "1280*720-50"
        custom_cvt = ""
        if screen_mode == "Custom":
            custom_cvt = CustomScreenMode(
                **{
                    key: display[key]
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
            ).to_cvt_string()
        return render_boot_partition_files(
            screen_mode=screen_mode,
            custom_cvt=custom_cvt,
            rom_filename=self._current_boot_rom_filename(),
            emu68_version=self.emu68_tab.get_emu68_version().value,
            usb_otg="poseidon" in self.packages_tab.resolution.selected,
            boot_settings=self._collect_emu68_boot_settings(),
        )

    def _collect_emu68_boot_settings(self):
        settings = self.emu68_tab.get_settings()
        settings["config_txt"].update(self.display_tab.get_emu68_boot_settings())
        return Emu68BootSettings.model_validate(settings)

    def _refresh_boot_files_preview(self):
        try:
            self.emu68_tab.set_preview_files(self._render_boot_files_preview())
        except Exception as error:
            self.emu68_tab.set_preview_error(f"Preview unavailable:\n{validation_message(error)}")

    def show_boot_preview(self):
        self._refresh_boot_files_preview()
        self.preview_dialog.show()
        self.preview_dialog.raise_()

    def collect_config(self):
        """Read adapters without editing the draft; validate at save/build boundaries."""
        ks = self.kickstart_tab.get_config()
        disp = self.display_tab.get_config()
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
        out = self.storage_controller.output
        wifi = self.network_tab.get_wifi_config()
        network = self.network_tab.get_network_settings()
        data = {
            "version": CURRENT_CONFIG_VERSION,
            "kickstart": {"version": ks["version"], "rom_directory": None},
            "install_media": {"directory": None},
            "asset_directories": [
                Path(p) for p in ks.get("asset_directories", []) if str(p).strip()
            ],
            "display": {
                "hdmi_mode": hdmi_mode,
                "custom": custom,
                "workbench_mode": disp["workbench_mode"],
                "workbench_theme": disp["workbench_theme"],
                "picasso96_archive": self.display_tab.get_picasso96_archive(),
            },
            "packages": self.packages_tab.get_config()
            + self.network_tab.extra_package_entries()
            + self.kickstart_tab.get_locale_entries(),
            "icon_set": self.kickstart_tab.get_icon_set(),
            "partitions": self.partitions_tab.get_config().model_dump(mode="python"),
            "ags_import": self.storage_controller.ags_config(),
            "output": out if out.get("path") else None,
            "network_stack": self.network_tab.get_network_stack(),
            "roadshow_archive": self.network_tab.get_roadshow_archive(),
            "miamidx_key_directory": self.network_tab.get_miamidx_key_directory(),
            "wifi": wifi.model_dump(mode="python") if wifi else None,
            "network": network.model_dump(mode="python"),
            "emu68_version": self.emu68_tab.get_emu68_version(),
            "emu68_boot": self._collect_emu68_boot_settings().model_dump(mode="python"),
        }
        config = BuildConfig.model_validate(data)
        self.config = config
        return config

    def _refresh_catalog(self):
        self.packages_tab.refresh_catalog()
        removed = self.kickstart_tab.refresh_catalog()
        if removed:
            self.packages_tab.catalog_notice.setText(
                self.packages_tab.catalog_notice.text()
                + "\nPreviously selected locales are no longer available: "
                + ", ".join(removed)
            )
        self.network_tab._refresh_roadshow_status()
        self.controller.refresh_context()
        self.controller.invalidate()

    def build_image(self):
        if self._build_active:
            return
        if self.stack.currentWidget() is not self.page_widgets["review"]:
            self.navigate("review")
            return
        snapshot, issues = self.controller.preflight()
        self.refresh_review()
        if snapshot is None:
            QMessageBox.warning(self, "Build checks", "\n".join(issue.message for issue in issues))
            return
        output = snapshot.output
        target = f"\nSD card: {output.flash_target}" if output.flash_target else ""
        warning = (
            "\nThe selected SD card will be erased."
            if output.type == OutputType.DEVICE or output.flash_target
            else ""
        )
        reply = QMessageBox.question(
            self,
            "Start build",
            f"Output: {output.path}{target}\nCapacity: {snapshot.partitions.disk_size / 1024**3:.2f} GiB{warning}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._build_active = True
        self.build_btn.setEnabled(False)
        self.start_tab.set_catalog_build_active(True)
        try:
            dialog = BuildProgressDialog(snapshot.model_copy(deep=True), self)
            dialog.start_build()
            dialog.exec()
            self.statusBar().showMessage(
                "Build complete" if dialog.success else "Build cancelled or failed"
            )
        finally:
            self._build_active = False
            self.start_tab.set_catalog_build_active(False)
            self.refresh_review()


def launch_gui():
    app = QApplication(sys.argv)
    app.setApplicationName("Emu68 Hatcher")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    launch_gui()
