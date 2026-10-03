"""archive extraction utilities"""

import gzip
import shutil
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from emu68hatcher.utils.host_tools import (
    find_7z,
    find_hst_imager,
    list_7z_member_sizes,
    run_7z,
    run_hst_extract,
)
from emu68hatcher.utils.paths import get_extracted_dir


class ArchiveFormat(str, Enum):
    """supported archive formats"""

    ZIP = "zip"
    SEVENZIP = "7z"
    LHA = "lha"
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    GZIP = "gz"
    UNKNOWN = "unknown"


@dataclass
class ExtractionResult:
    """result of an extraction operation"""

    output_dir: Path
    success: bool
    files_extracted: int
    error: str | None = None


# progress callback type
ExtractProgressCallback = Callable[[str, int, int], None]

# extensions recognized as extractable archives
ARCHIVE_EXTENSIONS = {".lha", ".lzh", ".zip", ".7z", ".tar", ".gz", ".tgz"}

# 8 GiB cap on uncompressed output to defuse zip/tar bombs - well above any legit asset
DEFAULT_MAX_EXTRACTED_BYTES = 8 * 1024 * 1024 * 1024


def _validate_member_path(name: str, output_dir: Path) -> None:
    """raise ValueError if extracting 'name' into output_dir would escape it"""
    if not name:
        raise ValueError("empty archive member name")
    p = Path(name)
    if p.is_absolute():
        raise ValueError(f"absolute path in archive: {name!r}")
    if any(part == ".." for part in p.parts):
        raise ValueError(f"parent-traversal in archive: {name!r}")
    output_root = output_dir.resolve()
    target = (output_root / p).resolve()
    if not target.is_relative_to(output_root):
        raise ValueError(f"archive member escapes output dir: {name!r}")


def _validate_tar_member(member: tarfile.TarInfo, output_dir: Path) -> None:
    """raise ValueError if a tar member is unsafe to extract"""
    _validate_member_path(member.name, output_dir)
    if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
        raise ValueError(f"refusing device/special member: {member.name!r}")
    if member.islnk() or member.issym():
        link = Path(member.linkname)
        if link.is_absolute() or any(part == ".." for part in link.parts):
            raise ValueError(f"refusing unsafe link: {member.name!r} -> {member.linkname!r}")


def detect_format(path: Path) -> ArchiveFormat:
    """detect format by extension, fall back to magic bytes"""
    suffix = path.suffix.lower()
    name = path.name.lower()

    # compound extensions first
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return ArchiveFormat.TAR_GZ
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return ArchiveFormat.TAR_BZ2

    ext_map = {
        ".zip": ArchiveFormat.ZIP,
        ".7z": ArchiveFormat.SEVENZIP,
        ".lha": ArchiveFormat.LHA,
        ".lzh": ArchiveFormat.LHA,
        ".tar": ArchiveFormat.TAR,
        ".gz": ArchiveFormat.GZIP,
    }

    if suffix in ext_map:
        return ext_map[suffix]

    try:
        with open(path, "rb") as f:
            magic = f.read(8)

        if magic[:4] == b"PK\x03\x04":
            return ArchiveFormat.ZIP
        if magic[:6] == b"7z\xbc\xaf\x27\x1c":
            return ArchiveFormat.SEVENZIP
        if magic[2:5] == b"-lh":
            return ArchiveFormat.LHA
        if magic[:2] == b"\x1f\x8b":
            return ArchiveFormat.GZIP

    except Exception:
        pass

    return ArchiveFormat.UNKNOWN


def extract_archive(
    archive_path: Path,
    output_dir: Path | None = None,
    progress_callback: ExtractProgressCallback | None = None,
) -> ExtractionResult:
    """extract archive to dir - detects format, picks extractor"""
    archive_path = Path(archive_path)

    if not archive_path.exists():
        return ExtractionResult(
            output_dir=Path(),
            success=False,
            files_extracted=0,
            error=f"Archive not found: {archive_path}",
        )

    fmt = detect_format(archive_path)

    if output_dir is None:
        base_name = archive_path.stem
        if base_name.endswith(".tar"):
            base_name = base_name[:-4]
        output_dir = get_extracted_dir() / base_name

    output_dir.mkdir(parents=True, exist_ok=True)

    extractors = {
        ArchiveFormat.ZIP: _extract_zip,
        ArchiveFormat.SEVENZIP: _extract_7z,
        ArchiveFormat.LHA: _extract_lha,
        ArchiveFormat.TAR: _extract_tar,
        ArchiveFormat.TAR_GZ: _extract_tar,
        ArchiveFormat.TAR_BZ2: _extract_tar,
        ArchiveFormat.GZIP: _extract_gzip,
    }

    extractor = extractors.get(fmt)
    if extractor is None:
        return ExtractionResult(
            output_dir=output_dir,
            success=False,
            files_extracted=0,
            error=f"Unsupported archive format: {fmt.value}",
        )

    try:
        files_extracted = extractor(archive_path, output_dir, progress_callback)
        return ExtractionResult(
            output_dir=output_dir,
            success=True,
            files_extracted=files_extracted,
        )
    except Exception as e:
        return ExtractionResult(
            output_dir=output_dir,
            success=False,
            files_extracted=0,
            error=str(e),
        )


def _extract_zip(
    archive_path: Path,
    output_dir: Path,
    progress_callback: ExtractProgressCallback | None = None,
    max_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
) -> int:
    """extract ZIP archive using python zipfile module"""
    with zipfile.ZipFile(archive_path, "r") as zf:
        infos = zf.infolist()
        total = len(infos)

        # validate every member up-front to avoid half-extracting a hostile archive
        cumulative = 0
        for info in infos:
            _validate_member_path(info.filename, output_dir)
            cumulative += info.file_size
            if cumulative > max_bytes:
                raise RuntimeError(f"zip would exceed {max_bytes} bytes uncompressed (bomb?)")

        for i, info in enumerate(infos):
            zf.extract(info, output_dir)
            if progress_callback:
                progress_callback(info.filename, i + 1, total)

        return total


def _extract_7z(
    archive_path: Path,
    output_dir: Path,
    progress_callback: ExtractProgressCallback | None = None,
) -> int:
    """extract 7z archive using 7z command"""
    seven_z = find_7z()
    if not seven_z:
        raise RuntimeError("7z not found. Please install p7zip.")
    _check_external_archive_size(seven_z, archive_path)

    result = run_7z(seven_z, ["x", "-y", f"-o{output_dir}", str(archive_path)])
    if result.returncode != 0:
        raise RuntimeError(f"7z extraction failed: {result.stderr}")

    return sum(1 for _ in output_dir.rglob("*") if _.is_file())


def _extract_lha(
    archive_path: Path,
    output_dir: Path,
    progress_callback: ExtractProgressCallback | None = None,
) -> int:
    """extract LHA/LZH - hst-imager first (preserves Latin-1), 7z fallback (e.g. Picasso96.lha)"""
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    seven_z = find_7z()
    if not seven_z:
        raise RuntimeError("7z not found. Cannot verify the LHA expanded size.")
    _check_external_archive_size(seven_z, archive_path)

    hst = find_hst_imager()
    if hst:
        result = run_hst_extract(
            hst,
            f"{archive_path}/*",
            str(output_dir) + "/",
            uaemetadata="None",
            recursive=True,
            timeout=None,
        )
        if result.returncode == 0:
            return sum(1 for _ in output_dir.rglob("*") if _.is_file())
        errors.append(
            f"hst-imager exit={result.returncode}: {(result.stderr or result.stdout).strip()[:300]}"
        )
    else:
        errors.append("hst-imager not found")

    result = run_7z(seven_z, ["x", "-y", f"-o{output_dir}", str(archive_path)])
    if result.returncode == 0:
        return sum(1 for _ in output_dir.rglob("*") if _.is_file())
    errors.append(
        f"{seven_z} exit={result.returncode}: {(result.stderr or result.stdout).strip()[:300]}"
    )

    raise RuntimeError("LHA extraction failed (" + "; ".join(errors) + ")")


def _check_external_archive_size(
    seven_z: Path,
    archive_path: Path,
    max_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
) -> None:
    total = sum(list_7z_member_sizes(seven_z, archive_path))
    if total > max_bytes:
        raise RuntimeError(f"archive would exceed {max_bytes} bytes uncompressed")


def _extract_tar(
    archive_path: Path,
    output_dir: Path,
    progress_callback: ExtractProgressCallback | None = None,
    max_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
) -> int:
    """Extract a TAR archive with automatic compression detection."""
    with tarfile.open(archive_path, "r:*") as tf:
        members = tf.getmembers()
        total = len(members)

        cumulative = 0
        for member in members:
            _validate_tar_member(member, output_dir)
            cumulative += getattr(member, "size", 0) or 0
            if cumulative > max_bytes:
                raise RuntimeError(f"tar would exceed {max_bytes} bytes uncompressed (bomb?)")

        for i, member in enumerate(members):
            # PEP 706 filter="data"; manual checks above are a fallback for older runtimes
            try:
                tf.extract(member, output_dir, filter="data")
            except TypeError:
                tf.extract(member, output_dir)
            if progress_callback:
                progress_callback(member.name, i + 1, total)

        return total


def _extract_gzip(
    archive_path: Path,
    output_dir: Path,
    progress_callback: ExtractProgressCallback | None = None,
) -> int:
    """extract gzip compressed file"""
    output_name = archive_path.stem  # strip .gz

    output_path = output_dir / output_name

    with gzip.open(archive_path, "rb") as gz:
        with open(output_path, "wb") as f:
            shutil.copyfileobj(gz, f)

    if progress_callback:
        progress_callback(output_name, 1, 1)

    return 1
