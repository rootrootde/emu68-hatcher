"""build config - pydantic models"""

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from emu68hatcher.config.ags_models import (
    AGSComponents,
    AGSImportConfig,
    AGSPartitionReservation,
    AGSRole,
)
from emu68hatcher.config.boot_models import (
    AntennaMode,
    BusTestMode,
    CmdlineTxtSettings,
    ConfigTxtSettings,
    Emu68BootSettings,
    FloppySwap,
    FramethrowerScaling,
    ReleaseToggle,
    UnicamDevice,
    Unit0Mode,
)
from emu68hatcher.config.display_models import (
    CustomScreenMode,
    DisplayConfig,
    WorkbenchScreenMode,
)
from emu68hatcher.config.network_models import (
    InterfaceIp,
    IpMode,
    NetworkSettings,
    NetworkStack,
    WifiConfig,
)
from emu68hatcher.config.partition_models import (
    AmigaPartition,
    Filesystem,
    MBRPartition,
    PartitionConfig,
)
from emu68hatcher.config.rgb2rtg_models import RGB2RTGConfig

__all__ = [
    "AGSImportConfig",
    "AGSComponents",
    "AGSPartitionReservation",
    "AGSRole",
    "AmigaPartition",
    "AntennaMode",
    "BuildConfig",
    "BusTestMode",
    "CmdlineTxtSettings",
    "ConfigTxtSettings",
    "CustomScreenMode",
    "DisplayConfig",
    "Emu68BootSettings",
    "Emu68Version",
    "Filesystem",
    "FloppySwap",
    "FramethrowerScaling",
    "InstallMediaConfig",
    "InterfaceIp",
    "IpMode",
    "KickstartConfig",
    "KickstartVersion",
    "MBRPartition",
    "NetworkSettings",
    "NetworkStack",
    "OutputConfig",
    "OutputType",
    "PackageConfig",
    "PartitionConfig",
    "ReleaseToggle",
    "UnicamDevice",
    "Unit0Mode",
    "WifiConfig",
    "WorkbenchScreenMode",
]

CURRENT_CONFIG_VERSION = "1.3.0"


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KickstartVersion(str, Enum):
    """kickstart / workbench versions the schema knows"""

    V3_1 = "3.1"
    V3_2 = "3.2"
    V3_2_2_1 = "3.2.2.1"
    V3_2_3 = "3.2.3"
    V3_9 = "3.9"


class OutputType(str, Enum):
    """output sink: file or device"""

    IMG = "img"
    DEVICE = "device"


_DEVICE_PATH_PREFIXES = ("/dev/disk", "/dev/sd", "/dev/mmcblk", "\\\\.\\PhysicalDrive")


def _is_device_path(p: str | Path) -> bool:
    return str(p).startswith(_DEVICE_PATH_PREFIXES)


class Emu68Version(str, Enum):
    """upstream Emu68 release to bundle on boot"""

    V1_0_7 = "1.0.7"
    V1_1_0_ALPHA_1 = "1.1.0-alpha.1"
    V1_1_0_BETA_1 = "1.1.0-beta.1"


######################
# sub-configurations #
######################


# versions the pipeline builds; adding here enables GUI + validator. needs adf_rules.yaml entry first
SUPPORTED_KICKSTARTS: tuple[KickstartVersion, ...] = (
    KickstartVersion.V3_1,
    KickstartVersion.V3_2,
    KickstartVersion.V3_2_2_1,
    KickstartVersion.V3_2_3,
    KickstartVersion.V3_9,
)


def _check_supported_version(label: str, v: KickstartVersion) -> KickstartVersion:
    """raise unless v is in SUPPORTED_KICKSTARTS"""
    if v not in SUPPORTED_KICKSTARTS:
        supported = ", ".join(k.value for k in SUPPORTED_KICKSTARTS)
        raise ValueError(
            f"{label} {v.value} is not yet supported by the build pipeline. Supported: {supported}."
        )
    return v


def _coerce_optional_path(v):
    """'' / None -> None; str -> Path"""
    if v is None or v == "":
        return None
    return Path(v) if isinstance(v, str) else v


class KickstartConfig(_ConfigModel):
    """kickstart ROM config"""

    version: KickstartVersion = KickstartVersion.V3_1
    rom_directory: Path | None = Field(
        default=None,
        description="Directory containing Kickstart ROM files. The correct ROM will be auto-detected.",
    )

    @field_validator("version")
    @classmethod
    def _check_supported(cls, v: KickstartVersion) -> KickstartVersion:
        return _check_supported_version("Kickstart", v)

    @field_validator("rom_directory", mode="before")
    @classmethod
    def convert_path(cls, v):
        return _coerce_optional_path(v)


class InstallMediaConfig(_ConfigModel):
    """OS install media config"""

    directory: Path | None = Field(
        default=None,
        description="Directory containing installation media (ADFs, ISOs, etc.). Files are auto-detected.",
    )

    @field_validator("directory", mode="before")
    @classmethod
    def convert_path(cls, v):
        return _coerce_optional_path(v)


class PackageConfig(_ConfigModel):
    """one package toggle"""

    name: str
    enabled: bool = True


class OutputConfig(_ConfigModel):
    """output config for the built image"""

    type: OutputType = OutputType.IMG
    # device targets stay plain strings: pathlib on windows 3.10/3.11 appends a trailing
    # backslash to \\.\PhysicalDriveN on str(), corrupting disk lookups and hst-imager argv
    path: Path | str = Field(description="Output path: .img file or /dev/diskN device")
    sparse: bool = Field(
        default=True,
        description="Allocate the .img as a sparse file (IMG mode only); huge disk-space win",
    )
    flash_target: str | None = Field(
        default=None,
        description="If set (IMG mode only), flash the built .img to this physical disk",
    )

    @field_validator("path", mode="before")
    @classmethod
    def convert_path(cls, v):
        if v is not None and _is_device_path(v):
            # rstrip also heals values a previous pathlib round-trip already corrupted
            return str(v).rstrip("\\/")
        return Path(v) if isinstance(v, str) else v

    @model_validator(mode="after")
    def validate_output(self):
        if self.type == OutputType.DEVICE:
            if not _is_device_path(self.path):
                raise ValueError(
                    f"DEVICE output requires a device path (e.g. /dev/disk4), got: {self.path}"
                )
            if self.flash_target:
                raise ValueError("flash_target is incompatible with DEVICE output")
        elif self.type == OutputType.IMG and _is_device_path(self.path):
            raise ValueError(
                f"IMG output rejects a device path (would overwrite the disk): {self.path}"
            )
        if self.flash_target and not _is_device_path(self.flash_target):
            raise ValueError(
                f"flash_target must be a physical disk device path, got: {self.flash_target}"
            )
        if self.flash_target and str(self.flash_target) == str(self.path):
            raise ValueError("flash_target cannot equal output path")
        return self


############################
# main Build Configuration #
############################


class BuildConfig(_ConfigModel):
    """full build config - JSON-serializable; drives the pipeline"""

    version: Literal["1.3.0"] = Field(
        default=CURRENT_CONFIG_VERSION,
        description="Config schema version",
    )

    # core settings
    kickstart: KickstartConfig = Field(default_factory=KickstartConfig)
    install_media: InstallMediaConfig = Field(default_factory=InstallMediaConfig)

    # directories scanned for both ROMs and ADFs/ISOs - the single source of truth
    asset_directories: list[Path] = Field(default_factory=list)

    # display settings
    display: DisplayConfig = Field(default_factory=DisplayConfig)

    # package selection
    packages: list[PackageConfig] = Field(default_factory=list)
    icon_set: str = "Default"
    ags_import: AGSImportConfig | None = None
    rgb2rtg: RGB2RTGConfig = Field(default_factory=RGB2RTGConfig)

    # partition layout
    partitions: PartitionConfig | None = None

    # output settings
    output: OutputConfig | None = None

    # network stack (None = no network stack installed)
    network_stack: NetworkStack | None = NetworkStack.ROADSHOW

    # optional path to a user-owned Roadshow archive; when set, replaces the bundled demo
    roadshow_archive: Path | None = None

    # optional folder containing MIAMI.KEY1, MIAMI.KEY2, and MIAMIDX.KEY
    miamidx_key_directory: Path | None = None

    # wifi creds - never serialized, never in repr
    wifi: WifiConfig | None = Field(default=None, exclude=True, repr=False)

    # per-interface IP mode + global gateway/DNS (creds live in `wifi` above)
    network: NetworkSettings = Field(default_factory=NetworkSettings)

    # boot
    emu68_version: Emu68Version = Field(
        default=Emu68Version.V1_0_7,
        description="upstream Emu68 release to bundle on the boot partition",
    )
    emu68_boot: Emu68BootSettings = Field(default_factory=Emu68BootSettings)

    @property
    def boot_device(self) -> str:
        """resolved boot partition device name - the single place stages read it from"""
        from emu68hatcher.config.defaults import DEFAULT_BOOT_DEVICE

        if self.partitions:
            return self.partitions.bootable_device_or_default
        return DEFAULT_BOOT_DEVICE

    @field_validator("roadshow_archive", "miamidx_key_directory", mode="before")
    @classmethod
    def _convert_network_path(cls, v):
        return _coerce_optional_path(v)

    @field_validator("asset_directories", mode="before")
    @classmethod
    def _convert_asset_directories(cls, v):
        if v is None:
            return []
        return [Path(p) if isinstance(p, str) else p for p in v]

    @model_validator(mode="after")
    def _migrate_legacy_asset_paths(self):
        # fold the old single rom_directory + install_media.directory fields into the new list
        # so configs from 0.2.x keep loading; dedupe by string identity to preserve order
        if self.asset_directories:
            return self
        merged: list[Path] = []
        seen: set[str] = set()
        for legacy in (self.kickstart.rom_directory, self.install_media.directory):
            if legacy is None:
                continue
            key = str(legacy)
            if key in seen:
                continue
            seen.add(key)
            merged.append(legacy)
        if merged:
            self.asset_directories = merged
        return self

    @model_validator(mode="after")
    def _check_framethrower_screen_mode(self):
        if (
            self.emu68_boot.config_txt.framethrower
            and self.emu68_boot.config_txt.unicam_device == UnicamDevice.C790
            and self.emu68_version == Emu68Version.V1_0_7
        ):
            raise ValueError("C790 requires Emu68 1.1 or later")
        if (
            self.emu68_boot.config_txt.framethrower
            and self.display.workbench_mode == WorkbenchScreenMode.NATIVE
        ):
            raise ValueError("Native-video capture requires a VideoCore Workbench screen mode")
        return self

    @model_validator(mode="after")
    def _check_ags_reservations(self):
        from emu68hatcher.config.ags_layout import validate_ags_layout

        if self.ags_import is None or not self.ags_import.enabled:
            errors = validate_ags_layout(self)
        elif self.ags_import.allocation_state == "ready":
            errors = validate_ags_layout(self)
        else:
            errors = ()
        if errors:
            raise ValueError("; ".join(errors))
        return self

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "version": CURRENT_CONFIG_VERSION,
                "kickstart": {
                    "version": "3.1",
                    "rom_directory": "/path/to/roms/",
                },
                "install_media": {
                    "directory": "/path/to/workbench/",
                },
                "display": {
                    "hdmi_mode": "1280*720-50",
                    "workbench_mode": "videocore_1280x720",
                },
                "packages": [
                    {"name": "whdload", "enabled": True},
                    {"name": "dopus418", "enabled": True},
                ],
                "icon_set": "GlowIcons",
                "partitions": {
                    "disk_size": 7600000000,
                    "layout": [
                        {"type": "fat32", "name": "EMU68BOOT", "size": 506000000},
                        {
                            "type": "id76",
                            "name": "AMIGA",
                            "size": 7093000000,
                            "amiga_partitions": [
                                {
                                    "device": "SDH0",
                                    "volume": "Workbench",
                                    "filesystem": "PFS3",
                                    "size": 506000000,
                                    "bootable": True,
                                    "priority": 0,
                                },
                            ],
                        },
                    ],
                },
                "output": {"type": "img", "path": "/home/user/amiga.img"},
            }
        },
    )
