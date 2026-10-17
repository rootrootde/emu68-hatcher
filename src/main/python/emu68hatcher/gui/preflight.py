"""Side-effect-free build checks over the window adapters."""

from dataclasses import dataclass

from pydantic import ValidationError

from emu68hatcher.builder.pipeline._selection import rgb2rtg_problems
from emu68hatcher.config.ags_layout import validate_ags_layout
from emu68hatcher.config.schema import BuildConfig, OutputType
from emu68hatcher.data.icon_sets import get_icon_set_extra_adf
from emu68hatcher.data.install_media import get_required_install_media
from emu68hatcher.data.package_loader import get_adf_rules_for_version
from emu68hatcher.data.package_selection import missing_optional_adfs
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


def run_preflight(controller) -> tuple[BuildConfig | None, list[ConfigurationIssue]]:
    window = controller.window
    storage = window.storage_controller
    rgb2rtg = window.emu68_tab.get_rgb2rtg()
    resolution = controller.resolution()
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
    output = window.output_tab.get_config()
    # device mode has no output path field; the SD card check above covers it
    if output["type"] != OutputType.DEVICE.value and not output.get("path"):
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
    try:
        wifi = window.network_tab.get_wifi_config()
    except ValueError as error:
        add(validation_message(error), "system", "network", "wifi_ssid")
    else:
        if window.network_tab.wifi_ssid.text().strip() and wifi is None:
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
        boot_roms = [row for row in panel.results["roms"] if row[0] == "boot"]
        if not boot_roms:
            add(
                "No matching boot ROM found. Rescan your asset directories.",
                "system",
                "amiga",
                "dir_list",
            )
        elif rgb2rtg.enabled and boot_roms[0][3] != "A1200":
            add(
                "RGB2RTG requires an A1200 ROM and A1200/PiStorm32-lite hardware.",
                "system",
                "amiga",
                "dir_list",
            )
        version = window.kickstart_tab.get_selected_version()
        icon_set = window.kickstart_tab.get_icon_set()
        found = {row[1] for row in panel.results["media"] if row[0] == "found"}
        required = set(get_required_install_media(version))
        missing = required - found
        if missing:
            add(
                "Missing installation media: " + ", ".join(sorted(missing)),
                "system",
                "amiga",
                "dir_list",
            )
        icon_adf = get_icon_set_extra_adf(icon_set, version)
        if icon_adf and icon_adf not in found:
            add(
                f"The selected icons require {icon_adf}. Add its media or choose another icon set.",
                "appearance",
                "appearance",
                "icon_set_combo",
            )
        optional_missing = missing_optional_adfs(
            get_adf_rules_for_version(version),
            resolution.selected,
            icon_set,
            found,
            required | {icon_adf} if icon_adf else required,
        )
        if optional_missing:
            add(
                "Selected software or languages require media: "
                + ", ".join(optional_missing)
                + ". Add the ADFs, or untick the packages on the Software page or the "
                "languages under System > AmigaOS & files > Languages.",
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
    if rgb2rtg.enabled:
        rgb2rtg_fields = {
            "emu68_version": ("system", "hardware", ""),
            "workbench_mode": ("appearance", "display", "workbench_mode_combo"),
            "framethrower": ("appearance", "display", "framethrower_check"),
            "emmc_unit0": ("system", "hardware", "emmc_unit_combo"),
            "extra_lines": ("system", "hardware", "extra_config_edit"),
            "archive": ("system", "hardware", "rgb2rtg_archive"),
        }
        boot = window.emu68_tab.get_settings()
        for key, message in rgb2rtg_problems(
            window.emu68_tab.get_emu68_version().value,
            window.display_tab.workbench_mode_combo.currentData(),
            window.display_tab.framethrower_check.isChecked(),
            boot["cmdline_txt"]["emmc_unit0"],
            [line.strip() for line in boot["config_txt"]["extra_lines"] if line.strip()],
            rgb2rtg.archive,
        ):
            add(message, *rgb2rtg_fields[key])
    for token, names in resolution.unsatisfiable.items():
        add(f"{token}: required by {', '.join(names)}", "software")
    if window.packages_tab.dropped_packages:
        add(
            "Saved packages are not in the catalog: "
            + ", ".join(window.packages_tab.dropped_packages),
            "software",
            severity="warning",
        )
    if any(issue.severity != "warning" for issue in issues):
        return None, issues
    try:
        config = controller.snapshot()
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
