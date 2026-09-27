"""Signed package and application update metadata."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from packaging.version import InvalidVersion, Version
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from emu68hatcher.data.package_schema import DownloadInfo, SourceType
from emu68hatcher.utils.paths import get_cache_dir
from emu68hatcher.utils.platform import OperatingSystem, PlatformInfo, get_platform_info

DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/rootrootde/emu68-hatcher/updates/manifest.json"
)
_REFERENCE_DIR = Path(__file__).parent / "reference"
_BUNDLED_MANIFEST_PATH = _REFERENCE_DIR / "update_manifest.json"
_PUBLIC_KEY_PATH = _REFERENCE_DIR / "update_manifest_public_key.pem"
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_GITHUB_REPO_RE = re.compile(r"^[\w][\w.-]*/[\w][\w.-]*$")
_SAFE_FILENAME_RE = re.compile(r"^[^/\\\x00-\x1f]+$")
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class PackageDownloadOverride(BaseModel):
    """Replacement download fields for one bundled package."""

    model_config = ConfigDict(extra="forbid")

    source: SourceType
    path: str | None = None
    url: AnyHttpUrl | None = None
    backup_url: AnyHttpUrl | None = None
    repo: str | None = None
    tag: str | None = Field(default=None, pattern=r"^[\w.\-+]+$")
    hash: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    filename: str

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        if not _SAFE_FILENAME_RE.fullmatch(value) or value in {".", ".."}:
            raise ValueError("filename must be a plain file name")
        return value

    @model_validator(mode="after")
    def _validate_source_fields(self):
        if self.source == SourceType.LOCAL:
            raise ValueError("remote manifests cannot select local package sources")
        if self.source == SourceType.AMINET and not self.path:
            raise ValueError("aminet source requires 'path'")
        if self.source == SourceType.GITHUB:
            if not self.repo or not _GITHUB_REPO_RE.fullmatch(self.repo):
                raise ValueError("github source requires a valid owner/repo")
        if self.source == SourceType.WEB and not self.url:
            raise ValueError("web source requires 'url'")
        return self

    def as_download_info(self) -> DownloadInfo:
        return DownloadInfo.model_validate(self.model_dump(mode="json", exclude_none=True))


class HatcherArtifact(BaseModel):
    """Installer offered for one host platform."""

    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    filename: str
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    size: int | None = Field(default=None, gt=0)

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        if not _SAFE_FILENAME_RE.fullmatch(value) or value in {".", ".."}:
            raise ValueError("filename must be a plain file name")
        return value


class HatcherRelease(BaseModel):
    """Latest published application release."""

    model_config = ConfigDict(extra="forbid")

    version: str
    release_url: AnyHttpUrl
    artifacts: dict[str, HatcherArtifact] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        try:
            Version(value)
        except InvalidVersion as error:
            raise ValueError("invalid application version") from error
        return value

    @field_validator("artifacts")
    @classmethod
    def _validate_platform_keys(
        cls, value: dict[str, HatcherArtifact]
    ) -> dict[str, HatcherArtifact]:
        allowed = {
            f"{os_name}-{arch}"
            for os_name in ("macos", "windows", "linux")
            for arch in ("x64", "arm64")
        }
        unknown = value.keys() - allowed
        if unknown:
            raise ValueError(f"unknown artifact platforms: {sorted(unknown)}")
        return value


class UpdateManifest(BaseModel):
    """Validated manifest payload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    revision: int = Field(ge=1)
    hatcher: HatcherRelease
    packages: dict[str, PackageDownloadOverride] = Field(default_factory=dict)

    @field_validator("packages")
    @classmethod
    def _validate_package_names(
        cls, value: dict[str, PackageDownloadOverride]
    ) -> dict[str, PackageDownloadOverride]:
        invalid = [name for name in value if not _IDENTIFIER_RE.fullmatch(name)]
        if invalid:
            raise ValueError(f"invalid package names: {sorted(invalid)}")
        return value


class ManifestSignature(BaseModel):
    """Detached signature stored beside the payload."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["ed25519"]
    key_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    value: str


class SignedManifest(BaseModel):
    """Signed JSON envelope."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    signature: ManifestSignature


@dataclass(frozen=True)
class ManifestSelection:
    manifest: UpdateManifest
    source: Literal["bundled", "cache", "remote"]
    changed: bool = False
    error: str | None = None
    checked: bool = False


_active_lock = threading.RLock()
_active_selection: ManifestSelection | None = None


def canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _load_public_key(path: Path = _PUBLIC_KEY_PATH) -> Ed25519PublicKey:
    key = load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("manifest public key is not Ed25519")
    return key


def verify_manifest_bytes(
    content: bytes,
    public_key_path: Path = _PUBLIC_KEY_PATH,
) -> UpdateManifest:
    if len(content) > _MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    try:
        raw = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid manifest JSON: {error}") from error
    envelope = SignedManifest.model_validate(raw)
    try:
        signature = base64.b64decode(envelope.signature.value, validate=True)
    except ValueError as error:
        raise ValueError("manifest signature is not valid base64") from error
    try:
        _load_public_key(public_key_path).verify(signature, canonical_payload(envelope.payload))
    except InvalidSignature as error:
        raise ValueError("manifest signature is invalid") from error
    return UpdateManifest.model_validate(envelope.payload)


def _cache_paths(cache_dir: Path | None = None) -> tuple[Path, Path]:
    root = cache_dir or get_cache_dir() / "updates"
    return root / "manifest.json", root / "manifest-meta.json"


def _read_verified(path: Path, public_key_path: Path) -> UpdateManifest:
    return verify_manifest_bytes(path.read_bytes(), public_key_path)


def initialize_manifest(
    *,
    bundled_path: Path = _BUNDLED_MANIFEST_PATH,
    public_key_path: Path = _PUBLIC_KEY_PATH,
    cache_dir: Path | None = None,
) -> ManifestSelection:
    global _active_selection

    bundled = _read_verified(bundled_path, public_key_path)
    selection = ManifestSelection(bundled, "bundled")
    cache_path, _ = _cache_paths(cache_dir)
    if cache_path.is_file():
        try:
            cached = _read_verified(cache_path, public_key_path)
            if cached.revision >= bundled.revision:
                selection = ManifestSelection(cached, "cache")
        except (OSError, ValueError):
            pass
    with _active_lock:
        _active_selection = selection
    return selection


def get_active_selection() -> ManifestSelection:
    with _active_lock:
        selection = _active_selection
    if selection is None:
        return initialize_manifest()
    return selection


def activate_manifest(selection: ManifestSelection) -> ManifestSelection:
    global _active_selection

    with _active_lock:
        current = _active_selection
        if current is not None and selection.manifest.revision < current.manifest.revision:
            raise ValueError("manifest revision is older than the active revision")
        _active_selection = selection
    return selection


def get_package_download_override(name: str) -> DownloadInfo | None:
    override = get_active_selection().manifest.packages.get(name.lower())
    return override.as_download_info() if override else None


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
    manifest: UpdateManifest | None = None,
    platform: PlatformInfo | None = None,
) -> HatcherArtifact | None:
    key = artifact_platform_key(platform)
    if key is None:
        return None
    active = manifest or get_active_selection().manifest
    return active.hatcher.artifacts.get(key)


def _read_response(response) -> bytes:
    length = response.headers.get("Content-Length")
    if length and int(length) > _MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    content = response.read(_MAX_MANIFEST_BYTES + 1)
    if len(content) > _MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    return content


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def _read_cache_headers(meta_path: Path) -> dict[str, str]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    headers = {}
    if isinstance(meta.get("etag"), str):
        headers["If-None-Match"] = meta["etag"]
    if isinstance(meta.get("last_modified"), str):
        headers["If-Modified-Since"] = meta["last_modified"]
    return headers


def _write_cache_headers(meta_path: Path, response) -> None:
    meta = {
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
    }
    _write_atomic(meta_path, (json.dumps(meta, sort_keys=True) + "\n").encode("utf-8"))


def check_remote_manifest(
    *,
    url: str | None = None,
    timeout: float = 15.0,
    cache_dir: Path | None = None,
    public_key_path: Path = _PUBLIC_KEY_PATH,
) -> ManifestSelection:
    current = get_active_selection()
    cache_path, meta_path = _cache_paths(cache_dir)
    headers = {"User-Agent": "Emu68 Hatcher/1.0", **_read_cache_headers(meta_path)}
    request = urllib.request.Request(
        url or os.environ.get("HATCHER_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = _read_response(response)
            manifest = verify_manifest_bytes(content, public_key_path)
            if manifest.revision < current.manifest.revision:
                raise ValueError("server manifest is older than the active revision")
            if manifest.revision == current.manifest.revision and manifest != current.manifest:
                raise ValueError("server manifest changed without a revision increase")
            _write_atomic(cache_path, content)
            _write_cache_headers(meta_path, response)
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return ManifestSelection(
                current.manifest,
                current.source,
                checked=True,
            )
        return ManifestSelection(
            current.manifest,
            current.source,
            error=f"HTTP {error.code} {error.reason}",
            checked=True,
        )
    except Exception as error:
        return ManifestSelection(
            current.manifest,
            current.source,
            error=str(error) or type(error).__name__,
            checked=True,
        )
    return ManifestSelection(
        manifest,
        "remote",
        changed=manifest.revision > current.manifest.revision,
        checked=True,
    )


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
