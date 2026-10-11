"""Jinja2-based Amiga startup script generator - Startup-Sequence, User-Startup etc. mirrors upstream imager"""

import re
from pathlib import Path

from emu68hatcher.builder.staging.scripts.templates import render_template
from emu68hatcher.config.boot_models import (
    AntennaMode,
    BusTestMode,
    CmdlineTxtSettings,
    Emu68BootSettings,
    FloppySwap,
    FramethrowerScaling,
    ReleaseToggle,
    UnicamDevice,
)
from emu68hatcher.data.data_manager import load_yaml_data
from emu68hatcher.utils.paths import ensure_dir


def generate_shell_startup() -> str:
    """generate Shell-Startup script"""
    return render_template("shell_startup.j2")


def get_screen_modes() -> list[dict]:
    """load screen modes from bundled YAML"""
    return load_yaml_data("screen_modes")


# kernel filenames per release. modern = pistorm32-lite/16, classic = old PiStorm. 1.1+ ships .gz
EMU68_KERNELS: dict[str, dict[str, str]] = {
    "1.0.7": {
        "modern": "Emu68-pistorm32lite",
        "classic": "Emu68-pistorm",
        "pistorm16": "Emu68-pistorm",
    },
    "1.1.0-alpha.1": {
        "modern": "Emu68-pistorm.gz",
        "classic": "Emu68-pistorm-classic.gz",
        "pistorm16": "Emu68-pistorm.gz",
    },
    "1.1.0-beta.1": {
        "modern": "Emu68-pistorm.gz",
        "classic": "Emu68-pistorm-classic.gz",
        "pistorm16": "Emu68-pistorm.gz",
    },
}


def _is_emu68_11(emu68_version: str) -> bool:
    return emu68_version.startswith("1.1")


def _uses_beta_overlays(emu68_version: str) -> bool:
    return emu68_version == "1.1.0-beta.1"


def _bus_test_enabled(settings: CmdlineTxtSettings, emu68_version: str) -> bool:
    if settings.bus_test == BusTestMode.FIRST_BOOT:
        return True
    if settings.bus_test == BusTestMode.DISABLED:
        return False
    return not _is_emu68_11(emu68_version)


def _emu68_overlay(settings: CmdlineTxtSettings, beta_overlays: bool) -> str | None:
    params = []
    if beta_overlays and settings.no_fpu:
        params.append("no_fpu")
    if beta_overlays and settings.vbr_move:
        params.append("vbr_move")
    if settings.fast_page_zero:
        params.append("FP0")
    if settings.chip_slowdown:
        params.append("SC")
        if settings.chip_slowdown_distance != 1:
            params.append(f"SCS={settings.chip_slowdown_distance}")
    if settings.dbf_slowdown:
        params.append("DBF")
    if settings.blitwait:
        params.append("BW")
    if not params:
        return None
    return "dtoverlay=emu68," + ",".join(params)


def _storage_overlay(
    name: str,
    unit0: str,
    low_speed: bool,
    clock_mhz: int | None,
) -> str:
    params = [f"unit0={unit0}"]
    if low_speed:
        params.append("low_speed")
    elif clock_mhz:
        params.append(f"clock={clock_mhz}")
    return f"dtoverlay={name}," + ",".join(params)


def _storage_overlays(settings: CmdlineTxtSettings, emu68_version: str) -> dict[str, str]:
    if not _uses_beta_overlays(emu68_version):
        return {}
    return {
        "sdhc": _storage_overlay(
            "sdhc",
            settings.sd_unit0.value,
            settings.sd_low_speed,
            settings.sd_clock_mhz,
        ),
        "emmc": _storage_overlay(
            "emmc",
            settings.emmc_unit0.value,
            settings.emmc_low_speed,
            settings.emmc_clock_mhz,
        ),
    }


def _config_overlays(
    settings: Emu68BootSettings,
    emu68_version: str,
    include_diagnostics: bool,
) -> list[str]:
    if not _is_emu68_11(emu68_version):
        if (
            settings.config_txt.framethrower
            and settings.config_txt.unicam_device == UnicamDevice.C790
        ):
            raise ValueError("C790 requires Emu68 1.1 or later")
        return []

    cmdline = settings.cmdline_txt
    beta_overlays = _uses_beta_overlays(emu68_version)
    overlays = []
    emu68 = _emu68_overlay(cmdline, beta_overlays)
    if emu68:
        overlays.append(emu68)

    if beta_overlays:
        if cmdline.z2_ram_size_mb is not None:
            overlays.append(f"dtoverlay=z2ram,size={cmdline.z2_ram_size_mb}")

    if include_diagnostics and _bus_test_enabled(cmdline, emu68_version):
        size = cmdline.bus_test_size_kb * 1024
        iterations = cmdline.bus_test_iterations
        overlays.append(f"dtoverlay=diagnostic,buptest,bupsize={size},bupiter={iterations}")

    if settings.config_txt.framethrower:
        config = settings.config_txt
        params = []
        if config.framethrower_start_on_boot:
            params.append("boot")
        if config.framethrower_scaling == FramethrowerScaling.SMOOTH:
            params.extend(("smooth", f"b={config.framethrower_b}", f"c={config.framethrower_c}"))
        elif config.framethrower_scaling == FramethrowerScaling.INTEGER:
            params.append("integer")
        if config.unicam_device == UnicamDevice.C790:
            params.append("type=c790")
        suffix = "," + ",".join(params) if params else ""
        overlays.append("dtoverlay=unicam" + suffix)

    return overlays


def _config_values(settings: Emu68BootSettings, emu68_version: str) -> dict:
    config = settings.config_txt
    is_11 = _is_emu68_11(emu68_version)
    is_alpha = emu68_version == "1.1.0-alpha.1"
    turbo = config.cpu_turbo == ReleaseToggle.ENABLED or (
        config.cpu_turbo == ReleaseToggle.DEFAULT and is_alpha
    )

    antenna = config.antenna
    if antenna == AntennaMode.DEFAULT:
        antenna = AntennaMode.EXTERNAL if is_alpha else None

    return {
        "boot_delay": config.boot_delay,
        "bootcode_delay": config.bootcode_delay,
        "avoid_warnings": (
            config.avoid_warnings if config.avoid_warnings is not None else (2 if is_11 else 1)
        ),
        "memory_limit_mb": (
            config.memory_limit_mb
            if config.memory_limit_mb is not None
            else (None if is_11 else 2048)
        ),
        "gpu_memory_mb": (
            config.gpu_memory_mb if config.gpu_memory_mb is not None else (None if is_11 else 32)
        ),
        "cpu_turbo": turbo,
        "arm_freq_mhz": config.arm_freq_mhz,
        "over_voltage": config.over_voltage,
        "arm_boost_pi4": config.arm_boost_pi4,
        "force_hdmi": config.force_hdmi,
        "antenna": antenna.value if antenna else None,
    }


def _cmdline_tokens(
    settings: Emu68BootSettings,
    emu68_version: str,
    include_bus_test: bool,
) -> list[str]:
    cmdline = settings.cmdline_txt
    beta_overlays = _uses_beta_overlays(emu68_version)
    tokens = []
    if not beta_overlays:
        tokens.extend(
            (
                f"sd.unit0={cmdline.sd_unit0.value}",
                f"emmc.unit0={cmdline.emmc_unit0.value}",
            )
        )
        if cmdline.sd_low_speed:
            tokens.append("sd.low_speed")
        elif cmdline.sd_clock_mhz:
            tokens.append(f"sd.clock={cmdline.sd_clock_mhz}")
        if cmdline.emmc_low_speed:
            tokens.append("emmc.low_speed")
        elif cmdline.emmc_clock_mhz:
            tokens.append(f"emmc.clock={cmdline.emmc_clock_mhz}")

    if cmdline.vbr_move and not beta_overlays:
        tokens.append("vbr_move")
    if not _is_emu68_11(emu68_version):
        if cmdline.fast_page_zero:
            tokens.append("fast_page_zero")
        if cmdline.chip_slowdown:
            tokens.append("chip_slowdown")
            if cmdline.chip_slowdown_distance != 1:
                tokens.append(f"cs_dist={cmdline.chip_slowdown_distance}")
        if cmdline.dbf_slowdown:
            tokens.append("dbf_slowdown")
        if cmdline.blitwait:
            tokens.append("blitwait")
        if settings.config_txt.framethrower:
            config = settings.config_txt
            if config.framethrower_start_on_boot:
                tokens.append("unicam.boot")
            if config.framethrower_scaling == FramethrowerScaling.SMOOTH:
                tokens.extend(
                    (
                        "unicam.smooth",
                        f"unicam.b={config.framethrower_b}",
                        f"unicam.c={config.framethrower_c}",
                    )
                )
            elif config.framethrower_scaling == FramethrowerScaling.INTEGER:
                tokens.append("unicam.integer")
    if cmdline.no_fpu and not beta_overlays:
        tokens.append("nofpu")
    if cmdline.limit_2g:
        tokens.append("limit_2g")
    if cmdline.disable_zorro3:
        tokens.append("z3_disable")
    if cmdline.z2_ram_size_mb is not None and not beta_overlays:
        tokens.append(f"z2_ram_size={cmdline.z2_ram_size_mb}")
    if cmdline.vc4_memory_mb is not None:
        tokens.append(f"vc4.mem={cmdline.vc4_memory_mb}")
    if cmdline.swap_df0 != FloppySwap.NONE:
        tokens.append(f"swap_df0_with_{cmdline.swap_df0.value}")
    if include_bus_test and not _is_emu68_11(emu68_version):
        tokens.extend(
            (
                f"buptest={cmdline.bus_test_size_kb}",
                f"bupiter={cmdline.bus_test_iterations}",
            )
        )
    tokens.extend(cmdline.extra_tokens)
    return tokens


def generate_config_txt(
    screen_mode: str = "1080*50",
    custom_cvt: str = "",
    rom_filename: str = "kick.rom",
    emu68_version: str = "1.0.7",
    usb_otg: bool = False,
    boot_settings: Emu68BootSettings | None = None,
    include_diagnostics: bool = True,
) -> str:
    """generate Emu68 boot config.txt with GPIO-based detection for PiStorm variants"""
    # load available screen modes from YAML
    available_modes = get_screen_modes()

    # handle legacy PAL/NTSC names
    mode_name_map = {
        "pal": "1080*50",
        "ntsc": "1080*60",
        "custom": "Custom",
    }
    screen_mode_normalized = mode_name_map.get(screen_mode.lower(), screen_mode)

    # determine if using custom mode
    is_custom_mode = screen_mode_normalized.lower() == "custom" and custom_cvt

    kernels = EMU68_KERNELS.get(emu68_version, EMU68_KERNELS["1.0.7"])
    boot_settings = boot_settings or Emu68BootSettings()

    content = render_template(
        "config_txt.j2",
        screen_mode=screen_mode_normalized,
        custom_cvt=custom_cvt,
        rom_filename=rom_filename,
        usb_otg=usb_otg,
        available_modes=available_modes,
        is_custom_mode=is_custom_mode,
        kernel_modern=kernels["modern"],
        kernel_classic=kernels["classic"],
        kernel_pistorm16=kernels["pistorm16"],
        storage_overlays=_storage_overlays(boot_settings.cmdline_txt, emu68_version),
        overlays=_config_overlays(boot_settings, emu68_version, include_diagnostics),
        extra_config_lines=boot_settings.config_txt.extra_lines,
        **_config_values(boot_settings, emu68_version),
    )
    return re.sub(r"\n{3,}", "\n\n", content)


def generate_boot_partition_files(
    staging_dir: Path,
    screen_mode: str = "1080*50",
    custom_cvt: str = "",
    rom_filename: str = "kick.rom",
    emu68_version: str = "1.0.7",
    usb_otg: bool = False,
    boot_settings: Emu68BootSettings | None = None,
) -> None:
    """generate files for the EMU68BOOT (FAT32) partition"""
    boot_dir = ensure_dir(staging_dir / "EMU68BOOT")

    for filename, content in render_boot_partition_files(
        screen_mode=screen_mode,
        custom_cvt=custom_cvt,
        rom_filename=rom_filename,
        emu68_version=emu68_version,
        usb_otg=usb_otg,
        boot_settings=boot_settings,
    ).items():
        (boot_dir / filename).write_text(content, encoding="utf-8", newline="\n")


def render_boot_partition_files(
    screen_mode: str = "1080*50",
    custom_cvt: str = "",
    rom_filename: str = "kick.rom",
    emu68_version: str = "1.0.7",
    usb_otg: bool = False,
    boot_settings: Emu68BootSettings | None = None,
) -> dict[str, str]:
    """Render the boot partition text files without writing them."""

    boot_settings = boot_settings or Emu68BootSettings()
    bus_test = _bus_test_enabled(boot_settings.cmdline_txt, emu68_version)

    config_txt = generate_config_txt(
        screen_mode=screen_mode,
        custom_cvt=custom_cvt,
        rom_filename=rom_filename,
        emu68_version=emu68_version,
        usb_otg=usb_otg,
        boot_settings=boot_settings,
    )

    base_tokens = _cmdline_tokens(boot_settings, emu68_version, False)
    active_tokens = _cmdline_tokens(boot_settings, emu68_version, bus_test)
    files = {
        "config.txt": config_txt,
        "cmdline.txt": " ".join(active_tokens) + "\n",
    }

    if bus_test and _is_emu68_11(emu68_version):
        files["configBAK.txt"] = generate_config_txt(
            screen_mode=screen_mode,
            custom_cvt=custom_cvt,
            rom_filename=rom_filename,
            emu68_version=emu68_version,
            usb_otg=usb_otg,
            boot_settings=boot_settings,
            include_diagnostics=False,
        )
    elif bus_test:
        files["cmdlineBAK.txt"] = " ".join(base_tokens) + "\n"

    return files
