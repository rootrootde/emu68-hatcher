"""Application release check and installer download."""

from __future__ import annotations

import hashlib
import urllib.request
from collections.abc import Callable
from pathlib import Path

from packaging.version import InvalidVersion, Version

from emu68hatcher.data.manifest_models import HatcherArtifact, UpdateManifestV1, UpdateManifestV2
from emu68hatcher.data.manifest_state import get_active_selection
from emu68hatcher.utils.platform import OperatingSystem, PlatformInfo, get_platform_info


def is_newer_version(current: str, available: str) -> bool:
    try:
        return Version(available) > Version(current)
    except InvalidVersion:
        return False


def artifact_platform_key(platform: PlatformInfo | None = None) -> str | None:
    info = platform or get_platform_info()
    os_name = {
        OperatingSystem.MACOS: "macos",
        OperatingSystem.WINDOWS: "windows",
        OperatingSystem.LINUX: "linux",
    }.get(info.os)
    if os_name is None or info.arch.value not in {"x64", "arm64"}:
        return None
    return f"{os_name}-{info.arch.value}"


def get_current_artifact(
    manifest: UpdateManifestV1 | UpdateManifestV2 | None = None,
    platform: PlatformInfo | None = None,
) -> HatcherArtifact | None:
    key = artifact_platform_key(platform)
    if key is None:
        return None
    active = manifest or get_active_selection().manifest
    return active.hatcher.artifacts.get(key)


def verify_sha256(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected.lower()


def _unused_download_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"no unused download filename near {path.name}")


def download_hatcher_artifact(
    artifact: HatcherArtifact,
    destination_dir: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 60.0,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    preferred = destination_dir / artifact.filename
    if preferred.is_file() and verify_sha256(preferred, artifact.sha256):
        return preferred
    destination = _unused_download_path(preferred)
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    request = urllib.request.Request(
        str(artifact.url),
        headers={"User-Agent": "Emu68 Hatcher/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length", 0))
            if artifact.size is not None and total and total != artifact.size:
                raise OSError(f"download size is {total} bytes, manifest says {artifact.size}")
            downloaded = 0
            with tmp.open("wb") as output:
                while True:
                    if cancelled and cancelled():
                        raise InterruptedError("download cancelled")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(downloaded, total or artifact.size or 0)
            if total and downloaded != total:
                raise OSError(f"incomplete response: received {downloaded} of {total} bytes")
            if artifact.size is not None and downloaded != artifact.size:
                raise OSError(f"download size is {downloaded} bytes, manifest says {artifact.size}")
            if not verify_sha256(tmp, artifact.sha256):
                raise ValueError("download checksum does not match the update manifest")
            tmp.replace(destination)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return destination
