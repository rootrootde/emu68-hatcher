"""Advanced Emu68 boot groups: firmware, storage, CPU, compatibility and manual overrides."""

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from emu68hatcher.config.boot_models import Emu68BootSettings
from emu68hatcher.config.schema import Emu68Version
from emu68hatcher.gui.forms import add_form_row, form_layout, set_widget_help
from emu68hatcher.gui.widgets import select_combo_by_data


def paired_field(primary: QWidget, secondary: QWidget) -> QWidget:
    field = QWidget()
    row = QHBoxLayout(field)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(primary, 1)
    row.addWidget(secondary)
    return field


class AdvancedBootSettings:
    """Advanced boot groups, laid out by the tab that owns the storage timing combo."""

    def __init__(
        self,
        storage_timing_combo: QComboBox,
        emu68_version: Emu68Version,
        on_custom_timing: Callable[[], None],
    ):
        self.storage_timing_combo = storage_timing_combo
        self.emu68_version = emu68_version
        self._on_custom_timing = on_custom_timing
        self._syncing_storage_timing = False
        storage_timing_combo.currentIndexChanged.connect(self.apply_storage_timing_selection)

        self.boot_group = self._create_boot_group()
        self.storage_group = self._create_storage_group()
        self.cpu_group = self._create_cpu_group()
        self.compatibility_group = self._create_emu68_compatibility_group()
        self.manual_overrides_group = self._create_manual_overrides_group()
        self.groups = (
            self.boot_group,
            self.storage_group,
            self.cpu_group,
            self.compatibility_group,
            self.manual_overrides_group,
        )

    def _create_boot_group(self) -> QGroupBox:
        boot_group = QGroupBox("Boot and firmware defaults")
        boot_form = form_layout(boot_group, top=True)
        labels: list[QLabel] = []

        self.boot_delay_spin = QSpinBox()
        self.boot_delay_spin.setRange(0, 10)
        self.boot_delay_spin.setSuffix(" s")
        add_form_row(
            boot_form,
            "start.elf delay:",
            self.boot_delay_spin,
            "Adds a firmware pause before loading the Emu68 kernel.",
            labels=labels,
        )

        self.bootcode_delay_spin = QSpinBox()
        self.bootcode_delay_spin.setRange(0, 10)
        self.bootcode_delay_spin.setValue(1)
        self.bootcode_delay_spin.setSuffix(" s")
        add_form_row(
            boot_form,
            "bootcode.bin delay:",
            self.bootcode_delay_spin,
            "Adds a pause before the firmware loads the boot files.",
            labels=labels,
        )

        self.avoid_warnings_combo = QComboBox()
        self.avoid_warnings_combo.addItem("Release default", None)
        self.avoid_warnings_combo.addItem("0 - show firmware warnings", 0)
        self.avoid_warnings_combo.addItem("1 - hide warning icons", 1)
        self.avoid_warnings_combo.addItem("2 - also ignore undervoltage throttling", 2)
        add_form_row(
            boot_form,
            "Firmware warnings:",
            self.avoid_warnings_combo,
            "Controls Raspberry Pi warning icons and undervoltage throttling.",
            labels=labels,
        )

        self.arm_boost_check = QCheckBox("arm_boost=1 for Pi 4 / CM4")
        self.arm_boost_check.setChecked(True)
        add_form_row(
            boot_form,
            "Pi 4 boost:",
            self.arm_boost_check,
            "Uses the boosted Pi 4 or CM4 ARM clock when supported by the firmware.",
            labels=labels,
        )

        return boot_group

    def _create_storage_group(self) -> QGroupBox:
        storage_group = QGroupBox("Storage")
        self.storage_layout = QGridLayout(storage_group)
        self.storage_layout.setHorizontalSpacing(20)

        self.storage_sd_fields = QWidget()
        labels: list[QLabel] = []
        sd_form = form_layout(self.storage_sd_fields, top=True)
        self.storage_emmc_fields = QWidget()
        emmc_form = form_layout(self.storage_emmc_fields, top=True)

        self.sd_unit_combo = QComboBox()
        for label, value in (("Off", "off"), ("Read only", "ro"), ("Read/write", "rw")):
            self.sd_unit_combo.addItem(label, value)
        self.sd_unit_combo.setCurrentIndex(2)
        add_form_row(
            sd_form,
            "SD card unit 0:",
            self.sd_unit_combo,
            "Sets whether Emu68 exposes the whole microSD card and allows writes to it.",
            labels=labels,
        )

        self.sd_low_speed_check = QCheckBox("Low speed")
        self.sd_low_speed_check.setChecked(True)
        self.sd_low_speed_check.toggled.connect(self.update_custom_fields)
        self.sd_low_speed_check.toggled.connect(self.sync_storage_timing_from_details)
        add_form_row(
            sd_form,
            "SD card timing:",
            self.sd_low_speed_check,
            "Disables the 50 MHz high-speed mode for cards that need conservative timing.",
            labels=labels,
        )

        self.sd_clock_spin = QSpinBox()
        self.sd_clock_spin.setRange(0, 100)
        self.sd_clock_spin.setSpecialValueText("Default clock")
        self.sd_clock_spin.setSuffix(" MHz")
        self.sd_clock_spin.valueChanged.connect(self.sync_storage_timing_from_details)
        add_form_row(
            sd_form,
            "SD card clock:",
            self.sd_clock_spin,
            "Overrides the high-speed SD clock when Low speed is off.",
            labels=labels,
        )

        self.emmc_unit_combo = QComboBox()
        for label, value in (("Off", "off"), ("Read only", "ro"), ("Read/write", "rw")):
            self.emmc_unit_combo.addItem(label, value)
        self.emmc_unit_combo.setCurrentIndex(2)
        add_form_row(
            emmc_form,
            "eMMC unit 0:",
            self.emmc_unit_combo,
            "Sets whether Emu68 exposes the whole eMMC device and allows writes to it.",
            labels=labels,
        )

        self.emmc_low_speed_check = QCheckBox("Low speed")
        self.emmc_low_speed_check.setChecked(True)
        self.emmc_low_speed_check.toggled.connect(self.update_custom_fields)
        self.emmc_low_speed_check.toggled.connect(self.sync_storage_timing_from_details)
        add_form_row(
            emmc_form,
            "eMMC timing:",
            self.emmc_low_speed_check,
            "Disables the 50 MHz high-speed mode for eMMC devices that need conservative timing.",
            labels=labels,
        )

        self.emmc_clock_spin = QSpinBox()
        self.emmc_clock_spin.setRange(0, 100)
        self.emmc_clock_spin.setSpecialValueText("Default clock")
        self.emmc_clock_spin.setSuffix(" MHz")
        self.emmc_clock_spin.valueChanged.connect(self.sync_storage_timing_from_details)
        add_form_row(
            emmc_form,
            "eMMC clock:",
            self.emmc_clock_spin,
            "Overrides the high-speed eMMC clock when Low speed is off.",
            labels=labels,
        )

        self.swap_df0_combo = QComboBox()
        self.swap_df0_combo.addItem("No swap", "none")
        self.swap_df0_combo.addItem("Swap DF0 with DF1", "df1")
        self.swap_df0_combo.addItem("Swap DF0 with DF2", "df2")
        self.swap_df0_combo.addItem("Swap DF0 with DF3", "df3")
        add_form_row(
            emmc_form,
            "Floppy drives:",
            self.swap_df0_combo,
            "Swaps DF0 with another floppy drive during Emu68 startup.",
            labels=labels,
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
        labels: list[QLabel] = []
        left_form = form_layout(self.cpu_left_fields, top=True)
        self.cpu_right_fields = QWidget()
        right_form = form_layout(self.cpu_right_fields, top=True)

        self.memory_limit_combo = QComboBox()
        self.memory_limit_combo.addItem("Release default", None)
        self.memory_limit_combo.addItem("No limit", 0)
        self.memory_limit_combo.addItem("Custom", "custom")
        self.memory_limit_combo.currentIndexChanged.connect(self.update_custom_fields)
        self.memory_limit_spin = QSpinBox()
        self.memory_limit_spin.setRange(128, 8192)
        self.memory_limit_spin.setValue(2048)
        self.memory_limit_spin.setSuffix(" MB")
        add_form_row(
            left_form,
            "Total RAM:",
            paired_field(self.memory_limit_combo, self.memory_limit_spin),
            "Limits ARM memory visible to the firmware. Release default follows Emu68.",
            labels=labels,
        )

        self.gpu_memory_combo = QComboBox()
        self.gpu_memory_combo.addItem("Release default", None)
        self.gpu_memory_combo.addItem("Omit gpu_mem", 0)
        self.gpu_memory_combo.addItem("Custom", "custom")
        self.gpu_memory_combo.currentIndexChanged.connect(self.update_custom_fields)
        self.gpu_memory_spin = QSpinBox()
        self.gpu_memory_spin.setRange(1, 512)
        self.gpu_memory_spin.setValue(32)
        self.gpu_memory_spin.setSuffix(" MB")
        add_form_row(
            left_form,
            "GPU memory:",
            paired_field(self.gpu_memory_combo, self.gpu_memory_spin),
            "Reserves general-purpose RAM for the VideoCore firmware.",
            labels=labels,
        )

        self.cpu_turbo_combo = QComboBox()
        self.cpu_turbo_combo.addItem("Release default", "default")
        self.cpu_turbo_combo.addItem("Disabled", "disabled")
        self.cpu_turbo_combo.addItem("Enabled", "enabled")
        self.cpu_turbo_combo.currentIndexChanged.connect(self.update_custom_fields)
        add_form_row(
            left_form,
            "CPU tuning:",
            self.cpu_turbo_combo,
            "Applies the custom ARM frequency and voltage settings when enabled.",
            labels=labels,
        )

        compatibility = QWidget()
        compatibility_grid = QGridLayout(compatibility)
        compatibility_grid.setContentsMargins(0, 0, 0, 0)
        compatibility_grid.setHorizontalSpacing(12)
        self.vbr_move_check = QCheckBox("Move VBR to Fast RAM")
        self.no_fpu_check = QCheckBox("Disable FPU")
        self.limit_2g_check = QCheckBox("Limit ARM RAM to 2 GB")
        self.disable_z3_check = QCheckBox("Disable Zorro III")
        set_widget_help(
            self.vbr_move_check,
            "Moves the exception vector table to Fast RAM. This can break floppy software.",
        )
        set_widget_help(
            self.no_fpu_check,
            "Disables the Emu68 FPU so FPU opcodes raise an exception.",
        )
        set_widget_help(
            self.limit_2g_check,
            "Limits mapped ARM memory to 2 GB for systems that cannot handle more.",
        )
        set_widget_help(
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
        add_form_row(
            left_form,
            "Compatibility:",
            compatibility,
            "Boot-time switches for memory, FPU, and expansion compatibility.",
            labels=labels,
        )

        self.arm_freq_spin = QSpinBox()
        self.arm_freq_spin.setRange(600, 2400)
        self.arm_freq_spin.setValue(1800)
        self.arm_freq_spin.setSuffix(" MHz")
        add_form_row(
            right_form,
            "ARM frequency:",
            self.arm_freq_spin,
            "Sets the ARM processor clock when CPU tuning is enabled.",
            labels=labels,
        )

        self.over_voltage_spin = QSpinBox()
        self.over_voltage_spin.setRange(-16, 8)
        self.over_voltage_spin.setValue(4)
        add_form_row(
            right_form,
            "Over voltage:",
            self.over_voltage_spin,
            "Sets the firmware voltage adjustment when CPU tuning is enabled.",
            labels=labels,
        )

        self.z2_ram_combo = QComboBox()
        self.z2_ram_combo.addItem("Default", None)
        for size in (0, 1, 2, 4, 8):
            self.z2_ram_combo.addItem(f"{size} MB", size)
        add_form_row(
            right_form,
            "Z2 RAM:",
            self.z2_ram_combo,
            "Sets the Zorro II RAM expansion size. Lower it if other Z2 devices need space.",
            labels=labels,
        )

        self.vc4_memory_spin = QSpinBox()
        self.vc4_memory_spin.setRange(0, 256)
        self.vc4_memory_spin.setSingleStep(2)
        self.vc4_memory_spin.setSpecialValueText("Default")
        self.vc4_memory_spin.setSuffix(" MB")
        add_form_row(
            right_form,
            "VC4 memory:",
            self.vc4_memory_spin,
            "Sets the VC4 memory reported to P96. This is separate from GPU memory.",
            labels=labels,
        )

        self.cpu_layout.addWidget(self.cpu_left_fields, 0, 0)
        self.cpu_layout.addWidget(self.cpu_right_fields, 1, 0)
        self.cpu_layout.setColumnStretch(0, 1)
        return cpu_group

    def _create_emu68_compatibility_group(self) -> QGroupBox:
        compatibility_group = QGroupBox("Emu68 compatibility")
        form = form_layout(compatibility_group, top=True)
        labels: list[QLabel] = []

        compatibility = QWidget()
        compatibility_grid = QGridLayout(compatibility)
        compatibility_grid.setContentsMargins(0, 0, 0, 0)
        compatibility_grid.setHorizontalSpacing(12)
        self.fast_page_zero_check = QCheckBox("Fast page zero")
        self.chip_slowdown_check = QCheckBox("CHIP slowdown")
        self.chip_slowdown_check.toggled.connect(self.update_custom_fields)
        self.dbf_slowdown_check = QCheckBox("DBF slowdown")
        self.blitwait_check = QCheckBox("Blitter waits")
        set_widget_help(
            self.fast_page_zero_check,
            "Maps the first 4 KB of RAM to fast ARM memory.",
        )
        set_widget_help(
            self.chip_slowdown_check,
            "Slows code running from CHIP memory for old software with busy-loop delays.",
        )
        set_widget_help(
            self.dbf_slowdown_check,
            "Slows self-branching DBF delay loops used by old games, demos, and replayers.",
        )
        set_widget_help(
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
        add_form_row(
            form,
            "Emu68 options:",
            compatibility,
            "Compatibility fixes for old software that depends on chipset timing.",
            labels=labels,
        )

        self.chip_distance_spin = QSpinBox()
        self.chip_distance_spin.setRange(1, 8)
        self.chip_distance_spin.setSuffix(" instruction(s)")
        add_form_row(
            form,
            "CHIP slowdown distance:",
            self.chip_distance_spin,
            "Applies CHIP slowdown to every nth instruction. Lower values are slower.",
            labels=labels,
        )

        self.bus_test_combo = QComboBox()
        self.bus_test_combo.addItem("Release default", "default")
        self.bus_test_combo.addItem("Disabled", "disabled")
        self.bus_test_combo.addItem("First boot", "first_boot")
        self.bus_test_combo.currentIndexChanged.connect(self.update_custom_fields)
        add_form_row(
            form,
            "PiStorm bus test:",
            self.bus_test_combo,
            "Checks the PiStorm bus with randomized CHIP-memory writes and reads.",
            labels=labels,
        )

        self.bus_test_size_spin = QSpinBox()
        self.bus_test_size_spin.setRange(1, 2048)
        self.bus_test_size_spin.setValue(512)
        self.bus_test_size_spin.setSuffix(" KB")
        add_form_row(
            form,
            "Test size:",
            self.bus_test_size_spin,
            "Sets how much CHIP memory the bus test writes and reads.",
            labels=labels,
        )

        self.bus_test_iterations_spin = QSpinBox()
        self.bus_test_iterations_spin.setRange(1, 9)
        self.bus_test_iterations_spin.setValue(1)
        add_form_row(
            form,
            "Test iterations:",
            self.bus_test_iterations_spin,
            "Sets the number of randomized data patterns used by the bus test.",
            labels=labels,
        )

        return compatibility_group

    def _create_manual_overrides_group(self) -> QGroupBox:
        advanced_group = QGroupBox("Manual overrides")
        layout = form_layout(advanced_group, top=True)
        labels: list[QLabel] = []

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
        add_form_row(
            layout,
            "config.txt:",
            self.extra_config_edit,
            "Adds firmware directives that are not covered by the controls above.",
            labels=labels,
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
        add_form_row(
            layout,
            "cmdline.txt:",
            self.extra_cmdline_edit,
            "Adds Emu68 boot tokens that are not covered by the controls above.",
            labels=labels,
        )
        return advanced_group

    def apply_storage_timing_selection(self, _index=None):
        if self._syncing_storage_timing:
            return

        mode = self.storage_timing_combo.currentData()
        if mode == "custom":
            self._on_custom_timing()
            self.sync_storage_timing_from_details()
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
        self.update_custom_fields()

    def sync_storage_timing_from_details(self, _value=None):
        if self._syncing_storage_timing:
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

    def set_release_defaults(
        self,
        version: Emu68Version,
        warning: str,
        memory: str,
        gpu: str,
        turbo: str,
        bus_test: str,
    ):
        self.emu68_version = version
        self.avoid_warnings_combo.setItemText(0, f"Release default ({warning})")
        self.memory_limit_combo.setItemText(0, f"Release default ({memory})")
        self.gpu_memory_combo.setItemText(0, f"Release default ({gpu})")
        self.cpu_turbo_combo.setItemText(0, f"Release default ({turbo})")
        self.bus_test_combo.setItemText(0, f"Release default ({bus_test})")

    def update_custom_fields(self, _value=None):
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

    def get_settings(self, antenna: str) -> dict:
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
                "antenna": antenna,
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
        self.sync_storage_timing_from_details()
        self.update_custom_fields()
