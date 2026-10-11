"""Emu68 boot-file settings."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReleaseToggle(str, Enum):
    DEFAULT = "default"
    DISABLED = "disabled"
    ENABLED = "enabled"


class AntennaMode(str, Enum):
    DEFAULT = "default"
    INTERNAL = "internal"
    EXTERNAL = "external"


class BusTestMode(str, Enum):
    DEFAULT = "default"
    DISABLED = "disabled"
    FIRST_BOOT = "first_boot"


class Unit0Mode(str, Enum):
    OFF = "off"
    READ_ONLY = "ro"
    READ_WRITE = "rw"


class FloppySwap(str, Enum):
    NONE = "none"
    DF1 = "df1"
    DF2 = "df2"
    DF3 = "df3"


class UnicamDevice(str, Enum):
    FRAMETHROWER = "ft"
    C790 = "c790"


class FramethrowerScaling(str, Enum):
    NONE = "none"
    SMOOTH = "smooth"
    INTEGER = "integer"


class ConfigTxtSettings(BaseModel):
    boot_delay: int = Field(default=0, ge=0, le=10)
    bootcode_delay: int = Field(default=1, ge=0, le=10)
    avoid_warnings: int | None = Field(default=None, ge=0, le=2)
    memory_limit_mb: int | None = Field(default=None, ge=0, le=8192)
    gpu_memory_mb: int | None = Field(default=None, ge=0, le=512)
    cpu_turbo: ReleaseToggle = ReleaseToggle.DEFAULT
    arm_freq_mhz: int = Field(default=1800, ge=600, le=2400)
    over_voltage: int = Field(default=4, ge=-16, le=8)
    arm_boost_pi4: bool = True
    force_hdmi: bool = True
    antenna: AntennaMode = AntennaMode.DEFAULT
    framethrower: bool = False
    unicam_device: UnicamDevice = UnicamDevice.FRAMETHROWER
    framethrower_start_on_boot: bool = True
    framethrower_scaling: FramethrowerScaling = FramethrowerScaling.SMOOTH
    framethrower_b: int = Field(default=200, ge=0, le=1000)
    framethrower_c: int = Field(default=400, ge=0, le=1000)
    extra_lines: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def migrate_framethrower_smooth(cls, value):
        if isinstance(value, dict) and "framethrower_smooth" in value:
            value = dict(value)
            smooth = value.pop("framethrower_smooth")
            value.setdefault("framethrower_scaling", "smooth" if smooth else "none")
        return value

    @field_validator("memory_limit_mb")
    @classmethod
    def validate_memory_limit(cls, value: int | None) -> int | None:
        if value is not None and value != 0 and value < 128:
            raise ValueError("config.txt memory limit must be 0 or at least 128 MB")
        return value

    @field_validator("extra_lines")
    @classmethod
    def validate_extra_lines(cls, lines: list[str]) -> list[str]:
        reserved = {"arm_64bit", "cmdline", "gpio", "initramfs", "kernel"}
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "\n" in line or "\r" in line:
                raise ValueError("config.txt additions must contain one directive per item")
            if len(line) > 98:
                raise ValueError("config.txt directives cannot exceed 98 characters")
            key = line.split("=", 1)[0].strip().lower()
            if key in reserved or key.startswith("initramfs "):
                raise ValueError(f"config.txt directive is managed by the builder: {key}")
            cleaned.append(line)
        return cleaned


class CmdlineTxtSettings(BaseModel):
    sd_unit0: Unit0Mode = Unit0Mode.READ_WRITE
    emmc_unit0: Unit0Mode = Unit0Mode.READ_WRITE
    sd_low_speed: bool = True
    emmc_low_speed: bool = True
    sd_clock_mhz: int | None = Field(default=None, ge=1, le=100)
    emmc_clock_mhz: int | None = Field(default=None, ge=1, le=100)
    vbr_move: bool = False
    fast_page_zero: bool = False
    chip_slowdown: bool = False
    chip_slowdown_distance: int = Field(default=1, ge=1, le=8)
    dbf_slowdown: bool = False
    blitwait: bool = False
    no_fpu: bool = False
    limit_2g: bool = False
    disable_zorro3: bool = False
    z2_ram_size_mb: int | None = None
    vc4_memory_mb: int | None = Field(default=None, ge=0, le=256)
    swap_df0: FloppySwap = FloppySwap.NONE
    bus_test: BusTestMode = BusTestMode.DEFAULT
    bus_test_size_kb: int = Field(default=512, ge=1, le=2048)
    bus_test_iterations: int = Field(default=1, ge=1, le=9)
    extra_tokens: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("z2_ram_size_mb")
    @classmethod
    def validate_z2_ram(cls, value: int | None) -> int | None:
        if value not in (None, 0, 1, 2, 4, 8):
            raise ValueError("Zorro II RAM must be 0, 1, 2, 4, or 8 MB")
        return value

    @field_validator("vc4_memory_mb")
    @classmethod
    def validate_vc4_memory(cls, value: int | None) -> int | None:
        if value is not None and value % 2:
            raise ValueError("VC4 memory must be an even number of MB")
        return value

    @field_validator("extra_tokens")
    @classmethod
    def validate_extra_tokens(cls, tokens: list[str]) -> list[str]:
        cleaned = []
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if any(char.isspace() for char in token):
                raise ValueError(f"cmdline.txt token contains whitespace: {token!r}")
            cleaned.append(token)
        return cleaned


class Emu68BootSettings(BaseModel):
    config_txt: ConfigTxtSettings = Field(default_factory=ConfigTxtSettings)
    cmdline_txt: CmdlineTxtSettings = Field(default_factory=CmdlineTxtSettings)

    model_config = ConfigDict(extra="forbid")
