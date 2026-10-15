"""Active update manifest selection, cache and remote check."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from emu68hatcher import __version__
from emu68hatcher.data.catalog import CatalogSnapshot, bundled_catalog
from emu68hatcher.data.catalog_manifest import (
    CATALOG_SCHEMA_VERSION,
    CatalogRelease,
    validate_client_catalog,
)
from emu68hatcher.data.manifest_models import (
    HatcherRelease,
    UpdateManifestV1,
    UpdateManifestV2,
)
from emu68hatcher.data.manifest_signing import (
    MAX_MANIFEST_BYTES,
    PUBLIC_KEY_PATH,
    verify_manifest_bytes,
)
from emu68hatcher.utils.paths import get_cache_dir

DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/rootrootde/emu68-hatcher/updates/manifest-v2.json"
)
_RELEASES_URL = "https://github.com/rootrootde/emu68-hatcher/releases"


@dataclass(frozen=True)
class ManifestSelection:
    manifest: UpdateManifestV1 | UpdateManifestV2
    source: Literal["bundled", "cache", "remote"]
    changed: bool = False
    error: str | None = None
    checked: bool = False
    catalog: CatalogSnapshot | None = None
    catalog_notice: str | None = None


_active_lock = threading.RLock()
_active_selection: ManifestSelection | None = None


def _cache_paths(cache_dir: Path | None = None) -> tuple[Path, Path]:
    root = cache_dir or get_cache_dir() / "updates"
    return root / "manifest-v2.json", root / "manifest-v2-meta.json"


def _read_verified(path: Path, public_key_path: Path) -> UpdateManifestV1 | UpdateManifestV2:
    return verify_manifest_bytes(path.read_bytes(), public_key_path)


def initialize_manifest(
    *,
    public_key_path: Path = PUBLIC_KEY_PATH,
    cache_dir: Path | None = None,
) -> ManifestSelection:
    global _active_selection

    # offline baseline: this release with nothing to download, catalog from the bundled YAML
    baseline = UpdateManifestV2(
        schema_version=2,
        revision=1,
        hatcher=HatcherRelease(version=__version__, release_url=_RELEASES_URL),
        catalogs=[
            CatalogRelease(
                id="bundled",
                revision=1,
                source_commit="0" * 40,
                min_hatcher_version=__version__,
                catalog_schema_version=CATALOG_SCHEMA_VERSION,
                **bundled_catalog().data().model_dump(mode="json", by_alias=True),
            )
        ],
    )
    selection = ManifestSelection(baseline, "bundled", catalog=bundled_catalog())
    cache_path, _ = _cache_paths(cache_dir)
    if cache_path.is_file():
        try:
            cached = _read_verified(cache_path, public_key_path)
            _validate_revision(cached, selection.manifest)
            candidate = _candidate_selection(cached, "cache", selection)
            if not candidate.catalog_notice:
                selection = candidate
        except (OSError, ValueError, ImportError):
            pass
    with _active_lock:
        _active_selection = selection
    return selection


def _validate_revision(manifest, previous):
    if manifest.schema_version != 2:
        raise ValueError("this client requires manifest schema 2")
    if manifest.revision < previous.revision:
        raise ValueError("server manifest is older than the active revision")
    if manifest.revision == previous.revision and manifest != previous:
        raise ValueError("server manifest changed without a revision increase")
    if isinstance(previous, UpdateManifestV2):
        old = {c.id: c for c in previous.catalogs}
        for catalog in manifest.catalogs:
            prior = old.get(catalog.id)
            if prior and (
                catalog.revision < prior.revision
                or (catalog.revision == prior.revision and catalog != prior)
            ):
                raise ValueError(f"catalog revision did not increase: {catalog.id}")


def _candidate_selection(manifest, source, current, *, checked=False):
    if not isinstance(manifest, UpdateManifestV2):
        raise ValueError("this client requires manifest schema 2")
    release = manifest.select_catalog()
    snapshot = current.catalog
    notice = None
    if release is None:
        notice = "The published package list is for another Hatcher version; previous list remains active."
    else:
        snapshot = release.snapshot()
        validate_client_catalog(snapshot)
    return ManifestSelection(
        manifest,
        source,
        manifest.revision > current.manifest.revision,
        checked=checked,
        catalog=snapshot,
        catalog_notice=notice,
    )


def get_active_selection() -> ManifestSelection:
    with _active_lock:
        selection = _active_selection
    if selection is None:
        return initialize_manifest()
    return selection


def activate_manifest(selection: ManifestSelection) -> ManifestSelection:
    global _active_selection

    with _active_lock:
        current = get_active_selection()
        _validate_revision(selection.manifest, current.manifest)
        candidate = _candidate_selection(
            selection.manifest, selection.source, current, checked=selection.checked
        )
        _active_selection = candidate
    return candidate


def _read_response(response) -> bytes:
    length = response.headers.get("Content-Length")
    if length and int(length) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    content = response.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    return content


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def _read_cache_headers(meta_path: Path, cache_path: Path, url: str) -> dict[str, str]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(meta, dict):
        return {}
    if (
        meta.get("url") != url
        or meta.get("sha256") != hashlib.sha256(cache_path.read_bytes()).hexdigest()
    ):
        return {}
    headers = {}
    if isinstance(meta.get("etag"), str):
        headers["If-None-Match"] = meta["etag"]
    if isinstance(meta.get("last_modified"), str):
        headers["If-Modified-Since"] = meta["last_modified"]
    return headers


def _write_cache_headers(meta_path: Path, response, cache_path: Path, url: str) -> None:
    meta = {
        "url": url,
        "sha256": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
    }
    _write_atomic(meta_path, (json.dumps(meta, sort_keys=True) + "\n").encode("utf-8"))


def check_remote_manifest(
    *,
    url: str | None = None,
    timeout: float = 15.0,
    cache_dir: Path | None = None,
    public_key_path: Path = PUBLIC_KEY_PATH,
) -> ManifestSelection:
    current = get_active_selection()
    cache_path, meta_path = _cache_paths(cache_dir)
    cached_selection = None
    cache_headers = {}
    try:
        cached = _read_verified(cache_path, public_key_path)
        _validate_revision(cached, current.manifest)
        cached_selection = _candidate_selection(cached, "cache", current, checked=True)
        if not cached_selection.catalog_notice:
            cache_headers = _read_cache_headers(
                meta_path,
                cache_path,
                url or os.environ.get("HATCHER_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL),
            )
    except (OSError, ValueError):
        pass
    headers = {"User-Agent": "Emu68 Hatcher/1.1", **cache_headers}
    request = urllib.request.Request(
        url or os.environ.get("HATCHER_UPDATE_MANIFEST_URL", DEFAULT_MANIFEST_URL),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = _read_response(response)
            manifest = verify_manifest_bytes(content, public_key_path)
            _validate_revision(manifest, current.manifest)
            if cached_selection:
                _validate_revision(manifest, cached_selection.manifest)
            candidate = _candidate_selection(manifest, "remote", current, checked=True)
            if not candidate.catalog_notice:
                _write_atomic(cache_path, content)
                _write_cache_headers(meta_path, response, cache_path, request.full_url)
    except urllib.error.HTTPError as error:
        if error.code == 304 and cache_headers and cached_selection:
            return cached_selection
        return ManifestSelection(
            current.manifest,
            current.source,
            catalog=current.catalog,
            error=f"HTTP {error.code} {error.reason}",
            checked=True,
        )
    except Exception as error:
        return ManifestSelection(
            current.manifest,
            current.source,
            catalog=current.catalog,
            error=str(error) or type(error).__name__,
            checked=True,
        )
    return candidate
