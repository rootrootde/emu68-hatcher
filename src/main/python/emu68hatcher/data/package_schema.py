"""pydantic models for package YAML defs"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]*$"


class SourceType(str, Enum):
    """package download source types"""

    AMINET = "aminet"
    GITHUB = "github"
    WEB = "web"
    LOCAL = "local"


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DownloadInfo(CatalogModel):
    """download configuration for a package"""

    source: SourceType

    # for aminet/web sources
    path: str | None = None  # aminet path like "util/libs/mui38usr.lha"
    url: str | None = None  # full URL for web downloads
    backup_url: str | None = None  # fallback URL

    # for GitHub sources
    repo: str | None = None  # format: "owner/repo"
    # restricted charset so a YAML tag can't smuggle ? # / etc. into the github api url
    tag: str | None = Field(default=None, pattern=r"^[\w.\-+]+$")
    check_latest_release: bool = True

    # verification
    hash: str | None = None  # MD5 hash for verification
    filename: str | None = None  # expected filename after download

    @model_validator(mode="after")
    def _check_required_fields_for_source(self):
        """validate per-source field combo - catch yaml typos before download"""
        if self.source == SourceType.AMINET and not self.path:
            raise ValueError("aminet source requires 'path'")
        if self.source == SourceType.GITHUB and not self.repo:
            raise ValueError("github source requires 'repo' (owner/name)")
        if self.source == SourceType.WEB and not self.url:
            raise ValueError("web source requires 'url'")
        return self


class InstallRule(CatalogModel):
    """rule for installing files from an extracted package"""

    # source pattern (glob) within extracted archive
    source: str = Field(alias="from")

    # destination path below the configured boot partition's staging root
    dest: str = Field(alias="to")

    # options
    recursive: bool = False
    rename: str | None = None  # rename file on install
    stack: int | None = None  # patch a .info icon's do_StackSize (workbench launch stack)
    xor_byte: int | None = Field(default=None, alias="xor", ge=0, le=255)

    model_config = ConfigDict(populate_by_name=True)


class RelocateRule(CatalogModel):
    """move a file already on SYS: (placed by the OS install) into another SYS: dir"""

    source: str = Field(alias="from")  # SYS:-relative path, e.g. "Tools/Commodities/ClickToFront"
    dest: str = Field(alias="to")  # SYS:-relative target dir, e.g. "WBStartup/"

    model_config = ConfigDict(populate_by_name=True)


class MenuEntry(CatalogModel):
    """workbench menu launcher for an installed app."""

    title: str  # label shown in the Workbench menu
    path: str  # executable path relative to SYS:, e.g. "Programs/IBrowse/IBrowse"
    menu: str = "Apps"  # Workbench menu title
    wb_launch: bool = False  # launch in Workbench mode instead of from the CLI
    selected_icons: bool = False  # pass selected Workbench icons to the launcher


class ScriptModification(CatalogModel):
    """script block appended (marker-wrapped) to an amiga script when the package is installed"""

    target: str = "S/User-Startup"  # script path relative to SYS:
    name: str  # marker label on the injected block
    content: str  # lines to append
    # None = always; True/False = only when the package's user-supplied archive is (not) configured
    when_user_archive: bool | None = None


class Package(CatalogModel):
    """complete package definition"""

    # identity
    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    friendly_name: str  # display name in UI
    group: str  # category group (System, Applications, Internet, etc.)
    description: str  # tooltip description

    # links (optional, surfaced in the GUI where relevant)
    purchase_url: str | None = None

    # compatibility
    versions: list[str] = Field(default_factory=list)  # kickstart versions
    emu68_versions: list[str] | None = None  # if set, gate on the chosen Emu68 release

    # installation behavior
    mandatory: bool = False  # must be installed
    default: bool = False  # pre-selected in UI (ignored when bundle is set)

    # parent Bundle.id (None = standalone); the bundle's GUI checkbox toggles all members
    bundle: str | None = None

    # dependency resolution. names may be a concrete package name or a virtual token
    # declared by some package's provides (a package implicitly provides its own name).
    requires: list[str] = Field(default_factory=list)  # hard deps, pulled in + locked
    recommends: list[str] = Field(default_factory=list)  # soft deps, pre-ticked but removable
    conflicts: list[str] = Field(default_factory=list)  # cannot coexist (symmetric at resolve)
    provides: list[str] = Field(default_factory=list)  # virtual capability tokens, e.g. "mui"

    # download configuration (None for local-only packages)
    download: DownloadInfo | None = None

    archive_package: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    # installation rules
    install: list[InstallRule] = Field(default_factory=list)

    # move already-staged OS files (e.g. drop a commodity into WBStartup)
    relocate: list[RelocateRule] = Field(default_factory=list)

    # script modifications
    scripts: list[ScriptModification] = Field(default_factory=list)

    # optional Workbench menu launchers
    menu_entry: MenuEntry | None = None
    menu_entries: list[MenuEntry] = Field(default_factory=list)

    @field_validator("requires", "recommends", "conflicts", "provides")
    @classmethod
    def _validate_tokens(cls, values: list[str]) -> list[str]:
        import re

        invalid = [value for value in values if not re.fullmatch(_IDENTIFIER_PATTERN, value)]
        if invalid:
            raise ValueError(f"tokens must be lowercase identifiers: {invalid}")
        normalized = [value.lower() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("capability list contains duplicate tokens")
        return values

    def matches_version(self, kickstart_version: str) -> bool:
        """check if package is compatible with a Kickstart version"""
        if not self.versions:
            return True  # no version restriction
        return kickstart_version in self.versions

    def matches_emu68(self, emu68_version: str | None) -> bool:
        """check if package applies to the selected Emu68 release; None = no restriction"""
        if not self.emu68_versions:
            return True
        if emu68_version is None:
            return True
        return emu68_version in self.emu68_versions


class Bundle(CatalogModel):
    """a group of packages presented as one GUI checkbox"""

    id: str  # stable identifier used by Package.bundle
    display_name: str  # label in the UI
    group: str  # category group (shared with Package.group)
    description: str  # tooltip / description column
    default: bool = False  # pre-selected in UI


# groups for organizing packages in UI
PACKAGE_GROUPS = [
    "System",
    "Drivers",
    "RTG",
    "Commodities",
    "Applications",
    "Utilities",
    "Fonts",
    "Internet",
    "Network",
    "Development",
    "Games",
    "Locale",
]


def _group_rank(pkg: Package) -> int:
    """sort rank of a package's group within PACKAGE_GROUPS; unknown groups sort last"""
    try:
        return PACKAGE_GROUPS.index(pkg.group)
    except ValueError:
        return len(PACKAGE_GROUPS)


########################
# ADF Extraction Rules #
########################


class ADFRule(CatalogModel):
    """rule for extracting files from an ADF disk image"""

    # source ADF identifier (e.g., "Workbench3_1", "Storage3_2")
    adf: str

    # source pattern within ADF (glob)
    source: str = Field(alias="from")

    # destination path on Amiga filesystem
    dest: str = Field(alias="to")

    # options
    recursive: bool = False
    rename: str | None = None

    # ordering
    sequence: int = 0

    # optional package association (if set, only extract if package enabled)
    package: str | None = None

    # mandatory flag - if False and package is set, only include if package enabled
    mandatory: bool = False

    # icon set filter (if set, only include for this icon set)
    icon_set: str | None = None

    model_config = ConfigDict(populate_by_name=True)
