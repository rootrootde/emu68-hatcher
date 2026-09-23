"""Build progress and typed phase outputs."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.host.elevation import ElevationToken
from emu68hatcher.data.install_media import IdentifiedInstallMedia

if TYPE_CHECKING:
    from emu68hatcher.builder.ags_source import AGSInventory


class BuildStage(str, Enum):
    INIT = "init"
    VALIDATE = "validate"
    SETUP_WORKSPACE = "setup_workspace"
    DOWNLOAD = "download"
    EXTRACT = "extract"
    CREATE_IMAGE = "create_image"
    INSTALL_WORKBENCH = "install_workbench"
    INSTALL_PACKAGES = "install_packages"
    CONFIGURE = "configure"
    IMPORT_AGS = "import_ags"
    INSTALL_EXTRAS = "install_extras"
    FINALIZE = "finalize"
    FLASH = "flash"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class BuildState:
    stage: BuildStage = BuildStage.INIT
    progress: float = 0.0
    message: str = ""
    elevation: ElevationToken | None = None
    disk_claim: object | None = None


@dataclass(frozen=True)
class ValidatedInputs:
    resolved_rom_path: Path
    resolved_rom_info: dict
    resolved_install_media: tuple[IdentifiedInstallMedia, ...]
    roadshow_archive_path: Path | None = None
    roadshow_archive_kind: str | None = None
    picasso96_archive_path: Path | None = None
    ags_inventory: "AGSInventory | None" = None


@dataclass(frozen=True)
class Workspace:
    validated: ValidatedInputs
    work_dir: Path
    staging_dir: Path
    downloads_dir: Path
    extracted_dir: Path
    workbench_dir: Path


@dataclass(frozen=True)
class DownloadedArtifacts:
    workspace: Workspace
    downloaded_files: dict[str, Path] = field(default_factory=dict)
    extracted_paths: dict[str, Path] = field(default_factory=dict)
    required_artifacts: set[str] = field(default_factory=set)
    required_boot_artifacts: set[str] = field(default_factory=set)
    required_packages: set[str] = field(default_factory=set)
    pfs3_handler_path: Path | None = None
    ffs_handler_path: Path | None = None


@dataclass(frozen=True)
class ExtractedArtifacts:
    downloaded: DownloadedArtifacts
    extracted_paths: dict[str, Path]


@dataclass(frozen=True)
class CreatedImage:
    extracted: ExtractedArtifacts
    image_path: Path | str
    final_output_path: Path | None = None

    @property
    def workspace(self) -> Workspace:
        return self.extracted.downloaded.workspace


@dataclass
class BuildResult:
    success: bool
    output_path: Path | str | None = None
    error: str | None = None


BuildProgressCallback = Callable[[BuildState], None]
BuildLogCallback = Callable[[str, str], None]
