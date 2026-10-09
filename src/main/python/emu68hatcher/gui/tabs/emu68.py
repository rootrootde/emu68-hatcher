"""Emu68 release, boot settings, and generated file preview."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.boot_models import Emu68BootSettings
from emu68hatcher.config.schema import Emu68Version
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.widgets import select_combo_by_data


class Emu68Tab(QWidget):
    """Emu68 boot settings and file preview."""

    settings_changed = Signal()
    emu68_version_changed = Signal(str)

    def __init__(
        self,
        emu68_version: Emu68Version = Emu68Version.V1_0_7,
        parent=None,
    ):
        super().__init__(parent)
        self.emu68_version = emu68_version
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        settings_pane = QWidget()
        settings_layout = QVBoxLayout(settings_pane)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(6)

        settings_header = QHBoxLayout()
        settings_header.setContentsMargins(0, 0, 0, 0)
        self.advanced_button = QPushButton("Show advanced settings")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._set_advanced_visible)
        settings_header.addWidget(self.advanced_button)
        settings_header.addStretch()

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.content_layout = page_layout(self.content)
        self.settings_scroll.setWidget(self.content)
        settings_layout.addWidget(self.settings_scroll)

        self._form_label_keys: dict[QFormLayout, object] = {}
        self._form_label_groups: dict[object, list[QLabel]] = {}

        self.release_group = self._create_release_group()
        self.content_layout.addWidget(self.release_group)

        self.settings_warning = QLabel(
            "⚠ These settings are experimental and may prevent Emu68 from booting."
        )
        self.settings_warning.setWordWrap(True)
        self.settings_warning.setProperty("alert", True)
        self.settings_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.hardware_group = self._create_hardware_group()
        self.content_layout.addWidget(self.hardware_group)
        self.content_layout.addLayout(settings_header)
        self.content_layout.addWidget(self.settings_warning)

        self.boot_group = self._create_boot_group()
        self.storage_group = self._create_storage_group()
        self.cpu_group = self._create_cpu_group()
        self.compatibility_group = self._create_emu68_compatibility_group()
        self.manual_overrides_group = self._create_manual_overrides_group()
        self._advanced_widgets = (
            self.settings_warning,
            self.boot_group,
            self.storage_group,
            self.cpu_group,
            self.compatibility_group,
            self.manual_overrides_group,
        )
        for widget in self._advanced_widgets[1:]:
            self.content_layout.addWidget(widget)
        self.content_layout.addStretch()

        self.preview_pane = QWidget()
        self.preview_pane.hide()
        preview_layout = QVBoxLayout(self.preview_pane)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(QLabel("Generated boot files"))
        preview_note = QLabel("Combined result of Emu68, Display and Software settings.")
        preview_note.setWordWrap(True)
        preview_layout.addWidget(preview_note)
        self.preview_tabs = QTabWidget()
        preview_layout.addWidget(self.preview_tabs)
        self._preview_editors: dict[str, QPlainTextEdit] = {}
        self._preview_tab_widgets: dict[str, QWidget] = {}
        self._preview_filenames: tuple[str, ...] = ()
        self.set_preview_error("Preview is not available yet.")

        layout.addWidget(settings_pane)

        self._settings_change_timer = QTimer(self)
        self._settings_change_timer.setSingleShot(True)
        self._settings_change_timer.setInterval(80)
        self._settings_change_timer.timeout.connect(self.settings_changed.emit)
        self._connect_settings_signals()
        self._set_advanced_visible(False)
        self._normalise_control_widths()
        self._sync_storage_timing_from_details()
        self.set_emu68_version(self.emu68_version)
        self._update_custom_fields()

    def _form_layout(self, parent: QWidget, label_group: str | None = None) -> QFormLayout:
        form = QFormLayout(parent)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        key = label_group if label_group is not None else form
        self._form_label_keys[form] = key
        self._form_label_groups.setdefault(key, [])
        return form

    def _add_form_row(
        self,
        form: QFormLayout,
        text: str,
        field: QWidget,
        help_text: str | None = None,
    ):
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if help_text:
            label.setToolTip(help_text)
            label.setAccessibleDescription(help_text)
            self._set_widget_help(field, help_text)
        labels = self._form_label_groups[self._form_label_keys[form]]
        labels.append(label)
        width = max(item.sizeHint().width() for item in labels)
        for item in labels:
            item.setFixedWidth(width)
        form.addRow(label, field)

    @staticmethod
    def _set_widget_help(widget: QWidget, help_text: str):
        for item in (widget, *widget.findChildren(QWidget)):
            if not item.toolTip():
                item.setToolTip(help_text)
            if not item.accessibleDescription():
                item.setAccessibleDescription(help_text)

    @staticmethod
    def _paired_field(primary: QWidget, secondary: QWidget) -> QWidget:
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(primary, 1)
        row.addWidget(secondary)
        return field

    def _normalise_control_widths(self):
        for combo in self.findChildren(QComboBox):
            combo.setMinimumWidth(180)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for spin in self.findChildren(QSpinBox):
            spin.setMinimumWidth(92)

    def _set_advanced_visible(self, visible: bool):
        for widget in self._advanced_widgets:
            widget.setVisible(visible)
        self.advanced_button.setText(
            "Hide advanced settings" if visible else "Show advanced settings"
        )

    def _connect_settings_signals(self):
        for combo in self.content.findChildren(QComboBox):
            combo.currentIndexChanged.connect(self._queue_settings_changed)
        for check in self.content.findChildren(QCheckBox):
            check.toggled.connect(self._queue_settings_changed)
        for spin in self.content.findChildren(QSpinBox):
            spin.valueChanged.connect(self._queue_settings_changed)
        self.extra_config_edit.textChanged.connect(self._queue_settings_changed)
        self.extra_cmdline_edit.textChanged.connect(self._queue_settings_changed)

    def _queue_settings_changed(self, _value=None):
        self._settings_change_timer.start()

    @staticmethod
    def _new_preview_editor() -> QPlainTextEdit:
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        return editor

    def _new_preview_section(self, heading: str, detail: str, filename: str) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(3)

        heading_label = QLabel(heading)
        heading_font = heading_label.font()
        heading_font.setBold(True)
        heading_label.setFont(heading_font)
        layout.addWidget(heading_label)

        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label)

        editor = self._new_preview_editor()
        self._preview_editors[filename] = editor
        layout.addWidget(editor)
        return section

    @staticmethod
    def _backup_filename(filename: str) -> str | None:
        if not filename.endswith(".txt"):
            return None
        return f"{filename[:-4]}BAK.txt"

    def _new_preview_page(self, filename: str, backup_filename: str | None) -> QWidget:
        if backup_filename is None:
            editor = self._new_preview_editor()
            self._preview_editors[filename] = editor
            return editor

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(
            self._new_preview_section(
                "First boot",
                f"{filename} is used for the first boot.",
                filename,
            )
        )
        splitter.addWidget(
            self._new_preview_section(
                "Subsequent boots",
                f"{backup_filename} becomes {filename} after the first boot.",
                backup_filename,
            )
        )
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 300])
        return splitter

    def set_preview_files(self, files: dict[str, str]):
        selected = self.preview_tabs.tabText(self.preview_tabs.currentIndex())
        filenames = tuple(files)
        if self._preview_filenames != filenames:
            self.preview_tabs.clear()
            self._preview_editors = {}
            self._preview_tab_widgets = {}
            self._preview_filenames = filenames
            consumed: set[str] = set()
            for filename in files:
                if filename in consumed:
                    continue
                backup_filename = self._backup_filename(filename)
                if backup_filename not in files:
                    backup_filename = None
                page = self._new_preview_page(filename, backup_filename)
                self._preview_tab_widgets[filename] = page
                self.preview_tabs.addTab(page, filename)
                consumed.add(filename)
                if backup_filename is not None:
                    consumed.add(backup_filename)

        for filename, content in files.items():
            editor = self._preview_editors[filename]
            if editor.toPlainText() == content:
                continue
            cursor_position = editor.textCursor().position()
            vertical_position = editor.verticalScrollBar().value()
            horizontal_position = editor.horizontalScrollBar().value()
            editor.setPlainText(content)
            cursor = editor.textCursor()
            cursor.setPosition(min(cursor_position, len(content)))
            editor.setTextCursor(cursor)
            editor.verticalScrollBar().setValue(vertical_position)
            editor.horizontalScrollBar().setValue(horizontal_position)

        selected_page = self._preview_tab_widgets.get(selected)
        if selected_page is not None:
            self.preview_tabs.setCurrentWidget(selected_page)

    def set_preview_error(self, message: str):
        self.preview_tabs.clear()
        self._preview_editors = {}
        self._preview_tab_widgets = {}
        self._preview_filenames = ()
        editor = self._new_preview_editor()
        editor.setPlainText(message)
        self.preview_tabs.addTab(editor, "Preview")

    def _create_release_group(self) -> QGroupBox:
        release_group = QGroupBox("Emu68 release")
        layout = QVBoxLayout(release_group)

        self.release_button_group = QButtonGroup(self)
        self.release_radio_stable = QRadioButton("1.0.7 (stable)")
        self.release_radio_beta = QRadioButton("1.1.0-beta.1 (beta)")
        self.release_radio_alpha = QRadioButton("1.1.0-alpha.1 (legacy)")
        self.release_radio_stable.setChecked(True)
        self.release_button_group.addButton(self.release_radio_stable)
        self.release_button_group.addButton(self.release_radio_beta)
        self.release_button_group.addButton(self.release_radio_alpha)
        self.release_button_group.buttonClicked.connect(self._select_emu68_version)
        layout.addWidget(self.release_radio_stable)
        layout.addWidget(self.release_radio_beta)
        layout.addWidget(self.release_radio_alpha)
        return release_group

    def _create_hardware_group(self) -> QGroupBox:
        hardware_group = QGroupBox("Hardware")
        form = self._form_layout(hardware_group, "summary")

        self.antenna_combo = QComboBox()
        self.antenna_combo.addItem("Release default", "default")
        self.antenna_combo.addItem("Internal (ant1)", "internal")
        self.antenna_combo.addItem("External (ant2)", "external")
        self._add_form_row(
            form,
            "CM4 antenna:",
            self.antenna_combo,
            "Selects the internal antenna or external connector on CM4 boards.",
        )

        self.storage_timing_combo = QComboBox()
        self.storage_timing_combo.addItem("Compatible (recommended)", "compatible")
        self.storage_timing_combo.addItem("Standard", "standard")
        self.storage_timing_combo.addItem("Custom (advanced)", "custom")
        self.storage_timing_combo.currentIndexChanged.connect(self._apply_storage_timing_selection)
        self._add_form_row(
            form,
            "Storage timing:",
            self.storage_timing_combo,
            "Compatible uses low-speed SD and eMMC timing. Standard uses normal clocks.",
        )

        return hardware_group

    def _create_boot_group(self) -> QGroupBox:
        boot_group = QGroupBox("Boot and firmware defaults")
        boot_form = self._form_layout(boot_group, "boot")

        self.boot_delay_spin = QSpinBox()
        self.boot_delay_spin.setRange(0, 10)
        self.boot_delay_spin.setSuffix(" s")
        self._add_form_row(
            boot_form,
            "start.elf delay:",
            self.boot_delay_spin,
            "Adds a firmware pause before loading the Emu68 kernel.",
        )

        self.bootcode_delay_spin = QSpinBox()
        self.bootcode_delay_spin.setRange(0, 10)
        self.bootcode_delay_spin.setValue(1)
        self.bootcode_delay_spin.setSuffix(" s")
        self._add_form_row(
            boot_form,
            "bootcode.bin delay:",
            self.bootcode_delay_spin,
            "Adds a pause before the firmware loads the boot files.",
        )

        self.avoid_warnings_combo = QComboBox()
        self.avoid_warnings_combo.addItem("Release default", None)
        self.avoid_warnings_combo.addItem("0 - show firmware warnings", 0)
        self.avoid_warnings_combo.addItem("1 - hide warning icons", 1)
        self.avoid_warnings_combo.addItem("2 - also ignore undervoltage throttling", 2)
        self._add_form_row(
            boot_form,
            "Firmware warnings:",
            self.avoid_warnings_combo,
            "Controls Raspberry Pi warning icons and undervoltage throttling.",
        )

        self.arm_boost_check = QCheckBox("arm_boost=1 for Pi 4 / CM4")
        self.arm_boost_check.setChecked(True)
        self._add_form_row(
            boot_form,
            "Pi 4 boost:",
            self.arm_boost_check,
            "Uses the boosted Pi 4 or CM4 ARM clock when supported by the firmware.",
        )

        return boot_group

    def _create_storage_group(self) -> QGroupBox:
        storage_group = QGroupBox("Storage")
        self.storage_layout = QGridLayout(storage_group)
        self.storage_layout.setHorizontalSpacing(20)

        self.storage_sd_fields = QWidget()
        sd_form = self._form_layout(self.storage_sd_fields, "storage")
        self.storage_emmc_fields = QWidget()
        emmc_form = self._form_layout(self.storage_emmc_fields, "storage")

        self.sd_unit_combo = QComboBox()
        for label, value in (("Off", "off"), ("Read only", "ro"), ("Read/write", "rw")):
            self.sd_unit_combo.addItem(label, value)
        self.sd_unit_combo.setCurrentIndex(2)
        self._add_form_row(
            sd_form,
            "SD card unit 0:",
            self.sd_unit_combo,
            "Sets whether Emu68 exposes the whole microSD card and allows writes to it.",
        )

        self.sd_low_speed_check = QCheckBox("Low speed")
        self.sd_low_speed_check.setChecked(True)
        self.sd_low_speed_check.toggled.connect(self._update_custom_fields)
        self.sd_low_speed_check.toggled.connect(self._sync_storage_timing_from_details)
        self._add_form_row(
            sd_form,
            "SD card timing:",
            self.sd_low_speed_check,
            "Disables the 50 MHz high-speed mode for cards that need conservative timing.",
        )

        self.sd_clock_spin = QSpinBox()
        self.sd_clock_spin.setRange(0, 100)
        self.sd_clock_spin.setSpecialValueText("Default clock")
        self.sd_clock_spin.setSuffix(" MHz")
        self.sd_clock_spin.valueChanged.connect(self._sync_storage_timing_from_details)
        self._add_form_row(
            sd_form,
            "SD card clock:",
            self.sd_clock_spin,
            "Overrides the high-speed SD clock when Low speed is off.",
        )

        self.emmc_unit_combo = QComboBox()
        for label, value in (("Off", "off"), ("Read only", "ro"), ("Read/write", "rw")):
            self.emmc_unit_combo.addItem(label, value)
        self.emmc_unit_combo.setCurrentIndex(2)
        self._add_form_row(
            emmc_form,
            "eMMC unit 0:",
            self.emmc_unit_combo,
            "Sets whether Emu68 exposes the whole eMMC device and allows writes to it.",
        )

        self.emmc_low_speed_check = QCheckBox("Low speed")
        self.emmc_low_speed_check.setChecked(True)
        self.emmc_low_speed_check.toggled.connect(self._update_custom_fields)
        self.emmc_low_speed_check.toggled.connect(self._sync_storage_timing_from_details)
        self._add_form_row(
            emmc_form,
            "eMMC timing:",
            self.emmc_low_speed_check,
            "Disables the 50 MHz high-speed mode for eMMC devices that need conservative timing.",
        )

        self.emmc_clock_spin = QSpinBox()
        self.emmc_clock_spin.setRange(0, 100)
        self.emmc_clock_spin.setSpecialValueText("Default clock")
        self.emmc_clock_spin.setSuffix(" MHz")
        self.emmc_clock_spin.valueChanged.connect(self._sync_storage_timing_from_details)
        self._add_form_row(
            emmc_form,
            "eMMC clock:",
            self.emmc_clock_spin,
            "Overrides the high-speed eMMC clock when Low speed is off.",
        )

        self.swap_df0_combo = QComboBox()
        self.swap_df0_combo.addItem("No swap", "none")
        self.swap_df0_combo.addItem("Swap DF0 with DF1", "df1")
        self.swap_df0_combo.addItem("Swap DF0 with DF2", "df2")
        self.swap_df0_combo.addItem("Swap DF0 with DF3", "df3")
        self._add_form_row(
            emmc_form,
            "Floppy drives:",
            self.swap_df0_combo,
            "Swaps DF0 with another floppy drive during Emu68 startup.",
        )

        self.storage_layout.addWidget(self.storage_sd_fields, 0, 0)
        self.storage_layout.addWidget(self.storage_emmc_fields, 1, 0)
        self.storage_layout.setColumnStretch(0, 1)

        return storage_group

    def _create_cpu_group(self) -> QGroupBox:
        cpu_group = QGroupBox("CPU and memory")
        self.cpu_layout = QGridLayout(cpu_group)
        self.cpu_layout.setHorizontalSpacing(20)

        self.cpu_left_fields = QWidget()
        left_form = self._form_layout(self.cpu_left_fields, "cpu-memory")
        self.cpu_right_fields = QWidget()
        right_form = self._form_layout(self.cpu_right_fields, "cpu-memory")

        self.memory_limit_combo = QComboBox()
        self.memory_limit_combo.addItem("Release default", None)
        self.memory_limit_combo.addItem("No limit", 0)
        self.memory_limit_combo.addItem("Custom", "custom")
        self.memory_limit_combo.currentIndexChanged.connect(self._update_custom_fields)
        self.memory_limit_spin = QSpinBox()
        self.memory_limit_spin.setRange(128, 8192)
        self.memory_limit_spin.setValue(2048)
        self.memory_limit_spin.setSuffix(" MB")
        self._add_form_row(
            left_form,
            "Total RAM:",
            self._paired_field(self.memory_limit_combo, self.memory_limit_spin),
            "Limits ARM memory visible to the firmware. Release default follows Emu68.",
        )

        self.gpu_memory_combo = QComboBox()
        self.gpu_memory_combo.addItem("Release default", None)
        self.gpu_memory_combo.addItem("Omit gpu_mem", 0)
        self.gpu_memory_combo.addItem("Custom", "custom")
        self.gpu_memory_combo.currentIndexChanged.connect(self._update_custom_fields)
        self.gpu_memory_spin = QSpinBox()
        self.gpu_memory_spin.setRange(16, 512)
        self.gpu_memory_spin.setValue(32)
        self.gpu_memory_spin.setSuffix(" MB")
        self._add_form_row(
            left_form,
            "GPU memory:",
            self._paired_field(self.gpu_memory_combo, self.gpu_memory_spin),
            "Reserves general-purpose RAM for the VideoCore firmware.",
        )

        self.cpu_turbo_combo = QComboBox()
        self.cpu_turbo_combo.addItem("Release default", "default")
        self.cpu_turbo_combo.addItem("Disabled", "disabled")
        self.cpu_turbo_combo.addItem("Enabled", "enabled")
        self.cpu_turbo_combo.currentIndexChanged.connect(self._update_custom_fields)
        self._add_form_row(
            left_form,
            "CPU tuning:",
            self.cpu_turbo_combo,
            "Applies the custom ARM frequency and voltage settings when enabled.",
        )

        compatibility = QWidget()
        compatibility_grid = QGridLayout(compatibility)
        compatibility_grid.setContentsMargins(0, 0, 0, 0)
        compatibility_grid.setHorizontalSpacing(12)
        self.vbr_move_check = QCheckBox("Move VBR to Fast RAM")
        self.no_fpu_check = QCheckBox("Disable FPU")
        self.limit_2g_check = QCheckBox("Limit ARM RAM to 2 GB")
        self.disable_z3_check = QCheckBox("Disable Zorro III")
        self._set_widget_help(
            self.vbr_move_check,
            "Moves the exception vector table to Fast RAM. This can break floppy software.",
        )
        self._set_widget_help(
            self.no_fpu_check,
            "Disables the Emu68 FPU so FPU opcodes raise an exception.",
        )
        self._set_widget_help(
            self.limit_2g_check,
            "Limits mapped ARM memory to 2 GB for systems that cannot handle more.",
        )
        self._set_widget_help(
            self.disable_z3_check,
            "Disables all Emu68 Zorro III boards, including SD and Unicam.",
        )
        for index, check in enumerate(
            (
                self.vbr_move_check,
                self.no_fpu_check,
                self.limit_2g_check,
                self.disable_z3_check,
            )
        ):
            compatibility_grid.addWidget(check, index // 2, index % 2)
        self._add_form_row(
            left_form,
            "Compatibility:",
            compatibility,
            "Boot-time switches for memory, FPU, and expansion compatibility.",
        )

        self.arm_freq_spin = QSpinBox()
        self.arm_freq_spin.setRange(600, 2400)
        self.arm_freq_spin.setValue(1800)
        self.arm_freq_spin.setSuffix(" MHz")
        self._add_form_row(
            right_form,
            "ARM frequency:",
            self.arm_freq_spin,
            "Sets the ARM processor clock when CPU tuning is enabled.",
        )

        self.over_voltage_spin = QSpinBox()
        self.over_voltage_spin.setRange(-16, 8)
        self.over_voltage_spin.setValue(4)
        self._add_form_row(
            right_form,
            "Over voltage:",
            self.over_voltage_spin,
            "Sets the firmware voltage adjustment when CPU tuning is enabled.",
        )

        self.z2_ram_combo = QComboBox()
        self.z2_ram_combo.addItem("Default", None)
        for size in (0, 1, 2, 4, 8):
            self.z2_ram_combo.addItem(f"{size} MB", size)
        self._add_form_row(
            right_form,
            "Z2 RAM:",
            self.z2_ram_combo,
            "Sets the Zorro II RAM expansion size. Lower it if other Z2 devices need space.",
        )

        self.vc4_memory_spin = QSpinBox()
        self.vc4_memory_spin.setRange(0, 256)
        self.vc4_memory_spin.setSingleStep(2)
        self.vc4_memory_spin.setSpecialValueText("Default")
        self.vc4_memory_spin.setSuffix(" MB")
        self._add_form_row(
            right_form,
            "VC4 memory:",
            self.vc4_memory_spin,
            "Sets the VC4 memory reported to P96. This is separate from GPU memory.",
        )

        self.cpu_layout.addWidget(self.cpu_left_fields, 0, 0)
        self.cpu_layout.addWidget(self.cpu_right_fields, 1, 0)
        self.cpu_layout.setColumnStretch(0, 1)
        return cpu_group

    def _create_emu68_compatibility_group(self) -> QGroupBox:
        compatibility_group = QGroupBox("Emu68 compatibility")
        form = self._form_layout(compatibility_group, "emu68-compatibility")

        compatibility = QWidget()
        compatibility_grid = QGridLayout(compatibility)
        compatibility_grid.setContentsMargins(0, 0, 0, 0)
        compatibility_grid.setHorizontalSpacing(12)
        self.fast_page_zero_check = QCheckBox("Fast page zero")
        self.chip_slowdown_check = QCheckBox("CHIP slowdown")
        self.chip_slowdown_check.toggled.connect(self._update_custom_fields)
        self.dbf_slowdown_check = QCheckBox("DBF slowdown")
        self.blitwait_check = QCheckBox("Blitter waits")
        self._set_widget_help(
            self.fast_page_zero_check,
            "Maps the first 4 KB of RAM to fast ARM memory.",
        )
        self._set_widget_help(
            self.chip_slowdown_check,
            "Slows code running from CHIP memory for old software with busy-loop delays.",
        )
        self._set_widget_help(
            self.dbf_slowdown_check,
            "Slows self-branching DBF delay loops used by old games, demos, and replayers.",
        )
        self._set_widget_help(
            self.blitwait_check,
            "Waits for the blitter before sensitive register writes in old software.",
        )
        for index, check in enumerate(
            (
                self.fast_page_zero_check,
                self.chip_slowdown_check,
                self.dbf_slowdown_check,
                self.blitwait_check,
            )
        ):
            compatibility_grid.addWidget(check, index // 2, index % 2)
        self._add_form_row(
            form,
            "Emu68 options:",
            compatibility,
            "Compatibility fixes for old software that depends on chipset timing.",
        )

        self.chip_distance_spin = QSpinBox()
        self.chip_distance_spin.setRange(1, 8)
        self.chip_distance_spin.setSuffix(" instruction(s)")
        self._add_form_row(
            form,
            "CHIP slowdown distance:",
            self.chip_distance_spin,
            "Applies CHIP slowdown to every nth instruction. Lower values are slower.",
        )

        self.bus_test_combo = QComboBox()
        self.bus_test_combo.addItem("Release default", "default")
        self.bus_test_combo.addItem("Disabled", "disabled")
        self.bus_test_combo.addItem("First boot", "first_boot")
        self.bus_test_combo.currentIndexChanged.connect(self._update_custom_fields)
        self._add_form_row(
            form,
            "PiStorm bus test:",
            self.bus_test_combo,
            "Checks the PiStorm bus with randomized CHIP-memory writes and reads.",
        )

        self.bus_test_size_spin = QSpinBox()
        self.bus_test_size_spin.setRange(1, 2048)
        self.bus_test_size_spin.setValue(512)
        self.bus_test_size_spin.setSuffix(" KB")
        self._add_form_row(
            form,
            "Test size:",
            self.bus_test_size_spin,
            "Sets how much CHIP memory the bus test writes and reads.",
        )

        self.bus_test_iterations_spin = QSpinBox()
        self.bus_test_iterations_spin.setRange(1, 9)
        self.bus_test_iterations_spin.setValue(1)
        self._add_form_row(
            form,
            "Test iterations:",
            self.bus_test_iterations_spin,
            "Sets the number of randomized data patterns used by the bus test.",
        )

        return compatibility_group

    def _create_manual_overrides_group(self) -> QGroupBox:
        advanced_group = QGroupBox("Manual overrides")
        layout = self._form_layout(advanced_group, "manual-overrides")

        self.extra_config_edit = QPlainTextEdit()
        self.extra_config_edit.setPlaceholderText(
            "One config.txt directive per line (for example dtoverlay=...)"
        )
        self.extra_config_edit.setToolTip(
            "1.1 overlays: emu68 (args, ICNT, CCRD, IRNG, SC, SCS, FP0, BW, DBF), "
            "diagnostic (buptest, bupiter, bupsize, membench, membase, memsize), "
            "unicam (boot, smooth, integer, full_width, full_height, width, height, bpp, "
            "mode, x, y, b, c, scaler, phase, lanes, aspect, order, type, ftmode; "
            "beta.1 spells them int, w, h, asp, ph and adds sc, scl). "
            "Turn off native-video capture in Display before adding a complete unicam overlay."
        )
        self.extra_config_edit.setMaximumHeight(100)
        self._add_form_row(
            layout,
            "config.txt:",
            self.extra_config_edit,
            "Adds firmware directives that are not covered by the controls above.",
        )

        self.extra_cmdline_edit = QLineEdit()
        self.extra_cmdline_edit.setPlaceholderText(
            "Space-separated cmdline.txt tokens (for example async_log fast_serial)"
        )
        self.extra_cmdline_edit.setToolTip(
            "Other upstream tokens include sd.verbose, emmc.verbose, debug, disassemble, "
            "async_log, fast_serial, enable_cache, checksum_rom, copy_rom, enable_c0_slow, "
            "enable_c8_slow, enable_d0_slow, move_slow_to_chip, ICNT, CCRD, and IRNG."
        )
        self._add_form_row(
            layout,
            "cmdline.txt:",
            self.extra_cmdline_edit,
            "Adds Emu68 boot tokens that are not covered by the controls above.",
        )
        return advanced_group

    def _apply_storage_timing_selection(self, _index=None):
        if getattr(self, "_syncing_storage_timing", False) or not hasattr(
            self, "sd_low_speed_check"
        ):
            return

        mode = self.storage_timing_combo.currentData()
        if mode == "custom":
            self.advanced_button.setChecked(True)
            self._sync_storage_timing_from_details()
            return
        if mode not in ("compatible", "standard"):
            return

        self._syncing_storage_timing = True
        low_speed = mode == "compatible"
        self.sd_low_speed_check.setChecked(low_speed)
        self.emmc_low_speed_check.setChecked(low_speed)
        self.sd_clock_spin.setValue(0)
        self.emmc_clock_spin.setValue(0)
        self._syncing_storage_timing = False
        self._update_custom_fields()

    def _sync_storage_timing_from_details(self, _value=None):
        if getattr(self, "_syncing_storage_timing", False) or not hasattr(
            self, "sd_low_speed_check"
        ):
            return

        if self.sd_low_speed_check.isChecked() and self.emmc_low_speed_check.isChecked():
            mode = "compatible"
        elif (
            not self.sd_low_speed_check.isChecked()
            and not self.emmc_low_speed_check.isChecked()
            and self.sd_clock_spin.value() == 0
            and self.emmc_clock_spin.value() == 0
        ):
            mode = "standard"
        else:
            mode = "custom"

        self._syncing_storage_timing = True
        select_combo_by_data(self.storage_timing_combo, mode)
        self._syncing_storage_timing = False

    def set_emu68_version(self, version: Emu68Version | str):
        self.emu68_version = version if isinstance(version, Emu68Version) else Emu68Version(version)
        if self.emu68_version == Emu68Version.V1_1_0_BETA_1:
            self.release_radio_beta.setChecked(True)
        elif self.emu68_version == Emu68Version.V1_1_0_ALPHA_1:
            self.release_radio_alpha.setChecked(True)
        else:
            self.release_radio_stable.setChecked(True)

        if self.emu68_version == Emu68Version.V1_1_0_ALPHA_1:
            defaults = ("2", "no limit", "omit", "enabled", "external", "disabled")
        elif self.emu68_version == Emu68Version.V1_1_0_BETA_1:
            defaults = ("2", "no limit", "omit", "disabled", "internal", "disabled")
        else:
            defaults = ("1", "2048 MB", "32 MB", "disabled", "internal", "first boot")

        warning, memory, gpu, turbo, antenna, bus_test = defaults
        self.avoid_warnings_combo.setItemText(0, f"Release default ({warning})")
        self.memory_limit_combo.setItemText(0, f"Release default ({memory})")
        self.gpu_memory_combo.setItemText(0, f"Release default ({gpu})")
        self.cpu_turbo_combo.setItemText(0, f"Release default ({turbo})")
        self.antenna_combo.setItemText(0, f"Release default ({antenna})")
        self.bus_test_combo.setItemText(0, f"Release default ({bus_test})")
        self._update_custom_fields()
        self._queue_settings_changed()
        self.emu68_version_changed.emit(self.emu68_version.value)

    def get_emu68_version(self) -> Emu68Version:
        if self.release_radio_beta.isChecked():
            return Emu68Version.V1_1_0_BETA_1
        if self.release_radio_alpha.isChecked():
            return Emu68Version.V1_1_0_ALPHA_1
        return Emu68Version.V1_0_7

    def _select_emu68_version(self, _button=None):
        self.set_emu68_version(self.get_emu68_version())

    def _update_custom_fields(self, _value=None):
        if not hasattr(self, "memory_limit_combo"):
            return
        self.memory_limit_spin.setEnabled(self.memory_limit_combo.currentData() == "custom")
        self.gpu_memory_spin.setEnabled(self.gpu_memory_combo.currentData() == "custom")
        cpu_custom = self.cpu_turbo_combo.currentData() == "enabled"
        self.arm_freq_spin.setEnabled(cpu_custom)
        self.over_voltage_spin.setEnabled(cpu_custom)
        self.sd_clock_spin.setEnabled(not self.sd_low_speed_check.isChecked())
        self.emmc_clock_spin.setEnabled(not self.emmc_low_speed_check.isChecked())
        self.chip_distance_spin.setEnabled(self.chip_slowdown_check.isChecked())
        is_11 = self.emu68_version.value.startswith("1.1")
        default_bus_test = not is_11 and self.bus_test_combo.currentData() == "default"
        bus_test = self.bus_test_combo.currentData() == "first_boot" or default_bus_test
        self.bus_test_size_spin.setEnabled(bus_test)
        self.bus_test_iterations_spin.setEnabled(bus_test)

    @staticmethod
    def _optional_custom_value(combo: QComboBox, spin: QSpinBox) -> int | None:
        value = combo.currentData()
        return spin.value() if value == "custom" else value

    def get_settings(self) -> dict:
        return {
            "config_txt": {
                "boot_delay": self.boot_delay_spin.value(),
                "bootcode_delay": self.bootcode_delay_spin.value(),
                "avoid_warnings": self.avoid_warnings_combo.currentData(),
                "memory_limit_mb": self._optional_custom_value(
                    self.memory_limit_combo, self.memory_limit_spin
                ),
                "gpu_memory_mb": self._optional_custom_value(
                    self.gpu_memory_combo, self.gpu_memory_spin
                ),
                "cpu_turbo": self.cpu_turbo_combo.currentData(),
                "arm_freq_mhz": self.arm_freq_spin.value(),
                "over_voltage": self.over_voltage_spin.value(),
                "arm_boost_pi4": self.arm_boost_check.isChecked(),
                "antenna": self.antenna_combo.currentData(),
                "extra_lines": self.extra_config_edit.toPlainText().splitlines(),
            },
            "cmdline_txt": {
                "sd_unit0": self.sd_unit_combo.currentData(),
                "emmc_unit0": self.emmc_unit_combo.currentData(),
                "sd_low_speed": self.sd_low_speed_check.isChecked(),
                "emmc_low_speed": self.emmc_low_speed_check.isChecked(),
                "sd_clock_mhz": self.sd_clock_spin.value() or None,
                "emmc_clock_mhz": self.emmc_clock_spin.value() or None,
                "vbr_move": self.vbr_move_check.isChecked(),
                "fast_page_zero": self.fast_page_zero_check.isChecked(),
                "chip_slowdown": self.chip_slowdown_check.isChecked(),
                "chip_slowdown_distance": self.chip_distance_spin.value(),
                "dbf_slowdown": self.dbf_slowdown_check.isChecked(),
                "blitwait": self.blitwait_check.isChecked(),
                "no_fpu": self.no_fpu_check.isChecked(),
                "limit_2g": self.limit_2g_check.isChecked(),
                "disable_zorro3": self.disable_z3_check.isChecked(),
                "z2_ram_size_mb": self.z2_ram_combo.currentData(),
                "vc4_memory_mb": self.vc4_memory_spin.value() or None,
                "swap_df0": self.swap_df0_combo.currentData(),
                "bus_test": self.bus_test_combo.currentData(),
                "bus_test_size_kb": self.bus_test_size_spin.value(),
                "bus_test_iterations": self.bus_test_iterations_spin.value(),
                "extra_tokens": self.extra_cmdline_edit.text().split(),
            },
        }

    def set_settings(self, settings: Emu68BootSettings):
        config = settings.config_txt
        cmdline = settings.cmdline_txt
        self.boot_delay_spin.setValue(config.boot_delay)
        self.bootcode_delay_spin.setValue(config.bootcode_delay)
        select_combo_by_data(self.avoid_warnings_combo, config.avoid_warnings)

        if config.memory_limit_mb in (None, 0):
            select_combo_by_data(self.memory_limit_combo, config.memory_limit_mb)
        else:
            select_combo_by_data(self.memory_limit_combo, "custom")
            self.memory_limit_spin.setValue(config.memory_limit_mb)
        if config.gpu_memory_mb in (None, 0):
            select_combo_by_data(self.gpu_memory_combo, config.gpu_memory_mb)
        else:
            select_combo_by_data(self.gpu_memory_combo, "custom")
            self.gpu_memory_spin.setValue(config.gpu_memory_mb)

        select_combo_by_data(self.cpu_turbo_combo, config.cpu_turbo.value)
        self.arm_freq_spin.setValue(config.arm_freq_mhz)
        self.over_voltage_spin.setValue(config.over_voltage)
        self.arm_boost_check.setChecked(config.arm_boost_pi4)
        select_combo_by_data(self.antenna_combo, config.antenna.value)
        self.extra_config_edit.setPlainText("\n".join(config.extra_lines))

        select_combo_by_data(self.sd_unit_combo, cmdline.sd_unit0.value)
        select_combo_by_data(self.emmc_unit_combo, cmdline.emmc_unit0.value)
        self.sd_low_speed_check.setChecked(cmdline.sd_low_speed)
        self.emmc_low_speed_check.setChecked(cmdline.emmc_low_speed)
        self.sd_clock_spin.setValue(cmdline.sd_clock_mhz or 0)
        self.emmc_clock_spin.setValue(cmdline.emmc_clock_mhz or 0)
        self.vbr_move_check.setChecked(cmdline.vbr_move)
        self.fast_page_zero_check.setChecked(cmdline.fast_page_zero)
        self.chip_slowdown_check.setChecked(cmdline.chip_slowdown)
        self.chip_distance_spin.setValue(cmdline.chip_slowdown_distance)
        self.dbf_slowdown_check.setChecked(cmdline.dbf_slowdown)
        self.blitwait_check.setChecked(cmdline.blitwait)
        self.no_fpu_check.setChecked(cmdline.no_fpu)
        self.limit_2g_check.setChecked(cmdline.limit_2g)
        self.disable_z3_check.setChecked(cmdline.disable_zorro3)
        select_combo_by_data(self.z2_ram_combo, cmdline.z2_ram_size_mb)
        self.vc4_memory_spin.setValue(cmdline.vc4_memory_mb or 0)
        select_combo_by_data(self.swap_df0_combo, cmdline.swap_df0.value)
        select_combo_by_data(self.bus_test_combo, cmdline.bus_test.value)
        self.bus_test_size_spin.setValue(cmdline.bus_test_size_kb)
        self.bus_test_iterations_spin.setValue(cmdline.bus_test_iterations)
        self.extra_cmdline_edit.setText(" ".join(cmdline.extra_tokens))
        self._sync_storage_timing_from_details()
        self._update_custom_fields()
