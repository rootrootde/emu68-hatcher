import base64
import hashlib
import json
import urllib.error
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from emu68hatcher.builder.host.download_catalog import get_package_downloads
from emu68hatcher.data.catalog import load_catalog_source
from emu68hatcher.data.package_loader import clear_package_caches
from emu68hatcher.data.update_manifest import (
    HatcherArtifact,
    ManifestSelection,
    UpdateManifestV2,
    activate_manifest,
    artifact_platform_key,
    canonical_payload,
    check_remote_manifest,
    download_hatcher_artifact,
    initialize_manifest,
    is_newer_version,
    verify_manifest_bytes,
)
from emu68hatcher.utils.platform import Architecture, OperatingSystem, PlatformInfo


def _payload(revision=1):
    data = load_catalog_source().model_dump(mode="json", by_alias=True)
    return {
        "schema_version": 2,
        "revision": revision,
        "hatcher": {
            "version": "0.9.0",
            "release_url": "https://github.com/rootrootde/emu68hatcher/releases",
            "artifacts": {},
        },
        "catalogs": [
            {
                "id": "test",
                "revision": revision,
                "source_commit": "1" * 40,
                "min_hatcher_version": "0",
                "catalog_schema_version": 1,
                **data,
            }
        ],
    }


@pytest.fixture
def signing_key(tmp_path):
    private = Ed25519PrivateKey.generate()
    public_path = tmp_path / "public.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private, public_path


def _signed(payload, private):
    signature = base64.b64encode(private.sign(canonical_payload(payload))).decode("ascii")
    return json.dumps(
        {
            "payload": payload,
            "signature": {
                "algorithm": "ed25519",
                "key_id": "updates-2026",
                "value": signature,
            },
        }
    ).encode()


@pytest.fixture(autouse=True)
def reset_manifest(tmp_path):
    initialize_manifest(cache_dir=tmp_path / "default-cache")
    clear_package_caches()
    yield
    initialize_manifest(cache_dir=tmp_path / "restored-cache")
    clear_package_caches()


def test_manifest_signature_and_schema(signing_key):
    private, public_path = signing_key
    content = _signed(_payload(), private)
    assert verify_manifest_bytes(content, public_path).revision == 1

    changed = json.loads(content)
    changed["payload"]["hatcher"]["version"] = "1.0.0"
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_manifest_bytes(json.dumps(changed).encode(), public_path)

    unsafe = _payload()
    unsafe["catalogs"][0]["packages"]["magicmenu"]["install"] = [
        {"from": "x", "to": "y", "unknown_rule": True}
    ]
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        verify_manifest_bytes(_signed(unsafe, private), public_path)


def test_invalid_cache_falls_back_to_bundled(tmp_path, signing_key):
    private, public_path = signing_key
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(_payload(1), private))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "manifest-v2.json").write_bytes(b"not a manifest")

    selection = initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert selection.source == "bundled"
    assert selection.manifest.revision == 1

    (cache_dir / "manifest-v2.json").write_bytes(_signed(_payload(2), private))
    selection = initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert selection.source == "cache"
    assert selection.manifest.revision == 2


class _Response:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {}
        self.position = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, count=-1):
        if count < 0:
            chunk = self.content[self.position :]
            self.position = len(self.content)
            return chunk
        chunk = self.content[self.position : self.position + count]
        self.position += len(chunk)
        return chunk


def test_remote_manifest_is_validated_and_cached(tmp_path, signing_key, monkeypatch):
    private, public_path = signing_key
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(_payload(1), private))
    cache_dir = tmp_path / "cache"
    initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    remote = _signed(_payload(2), private)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(remote, {"ETag": '"two"'}),
    )

    selection = check_remote_manifest(
        url="https://example.test/manifest.json",
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert selection.source == "remote"
    assert selection.changed
    assert selection.manifest.revision == 2
    assert (cache_dir / "manifest-v2.json").read_bytes() == remote


def test_remote_failure_keeps_active_manifest(tmp_path, signing_key, monkeypatch):
    private, public_path = signing_key
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(_payload(1), private))
    cache_dir = tmp_path / "cache"
    initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )

    def fail(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    selection = check_remote_manifest(
        url="https://example.test/manifest.json",
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert selection.source == "bundled"
    assert selection.manifest.revision == 1
    assert "offline" in selection.error
    assert not (cache_dir / "manifest-v2.json").exists()


def test_not_modified_uses_cached_manifest(tmp_path, signing_key, monkeypatch):
    private, public_path = signing_key
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(_payload(1), private))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = _signed(_payload(2), private)
    (cache_dir / "manifest-v2.json").write_bytes(cached)
    (cache_dir / "manifest-v2-meta.json").write_text(
        json.dumps(
            {
                "etag": '"two"',
                "sha256": hashlib.sha256(cached).hexdigest(),
                "url": "https://example.test/manifest.json",
            }
        ),
        encoding="utf-8",
    )
    initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )

    def not_modified(request, **_kwargs):
        assert request.get_header("If-none-match") == '"two"'
        raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", not_modified)
    selection = check_remote_manifest(
        url="https://example.test/manifest.json",
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert selection.source == "cache"
    assert selection.checked
    assert selection.manifest.revision == 2
    assert selection.error is None


def test_bad_remote_signature_does_not_replace_cache(tmp_path, signing_key, monkeypatch):
    private, public_path = signing_key
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(_payload(1), private))
    cache_dir = tmp_path / "cache"
    initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    other_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(_signed(_payload(2), other_key)),
    )

    selection = check_remote_manifest(
        url="https://example.test/manifest.json",
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert selection.source == "bundled"
    assert "signature is invalid" in selection.error
    assert not (cache_dir / "manifest-v2.json").exists()


def test_same_revision_cannot_change_content(tmp_path, signing_key, monkeypatch):
    private, public_path = signing_key
    bundled_payload = _payload(3)
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(bundled_payload, private))
    cache_dir = tmp_path / "cache"
    initialize_manifest(
        bundled_path=bundled,
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    changed_payload = deepcopy(bundled_payload)
    changed_payload["hatcher"]["version"] = "1.0.0"
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(_signed(changed_payload, private)),
    )

    selection = check_remote_manifest(
        url="https://example.test/manifest.json",
        public_key_path=public_path,
        cache_dir=cache_dir,
    )
    assert "without a revision increase" in selection.error
    assert selection.manifest.hatcher.version == "0.9.0"


def test_version_and_platform_selection():
    assert is_newer_version("0.9.0", "1.0.0")
    assert is_newer_version("1.0.0rc1", "1.0.0")
    assert not is_newer_version("1.0.0", "1.0.0rc1")
    assert not is_newer_version("invalid", "1.0.0")

    platform = PlatformInfo(OperatingSystem.MACOS, Architecture.ARM64, "test", False)
    assert artifact_platform_key(platform) == "macos-arm64"
    unknown = PlatformInfo(OperatingSystem.UNKNOWN, Architecture.X64, "test", False)
    assert artifact_platform_key(unknown) is None


def test_full_package_reaches_download_catalog(tmp_path):
    selection = initialize_manifest(cache_dir=tmp_path / "cache")
    payload = _payload(selection.manifest.revision + 1)
    payload["catalogs"][0]["packages"]["magicmenu"]["download"] = {
        "source": "web",
        "url": "https://downloads.example.test/MagicMenu.lha",
        "hash": "0123456789abcdef0123456789abcdef",
        "filename": "MagicMenu-fixed.lha",
    }
    payload["catalogs"][0]["packages"]["magicmenu"]["install"] = [{"from": "new/Menu", "to": "C"}]
    manifest = UpdateManifestV2.model_validate(payload)
    activate_manifest(ManifestSelection(manifest, "remote", changed=True, checked=True))
    clear_package_caches()

    item = get_package_downloads(["magicmenu"])[0]
    assert item.url == "https://downloads.example.test/MagicMenu.lha"
    assert item.filename == "MagicMenu-fixed.lha"
    assert item.expected_hash == "0123456789abcdef0123456789abcdef"
    from emu68hatcher.data.package_loader import get_package_by_name

    assert get_package_by_name("magicmenu").install[0].source == "new/Menu"


def test_application_download_is_atomic_and_verified(tmp_path, monkeypatch):
    content = b"verified installer"
    artifact = HatcherArtifact(
        url="https://example.test/installer.dmg",
        filename="installer.dmg",
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(content, {"Content-Length": str(len(content))}),
    )
    path = download_hatcher_artifact(artifact, tmp_path)
    assert path.read_bytes() == content
    assert not (tmp_path / "installer.dmg.tmp").exists()

    broken = artifact.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ValueError, match="checksum"):
        download_hatcher_artifact(broken, tmp_path)
    assert not (tmp_path / "installer-1.dmg.tmp").exists()


def test_package_paths_cannot_leave_staging_through_links(tmp_path):
    from emu68hatcher.builder.staging.files import resolve_source_path, resolve_staging_path

    root = tmp_path / "staging"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "file").write_bytes(b"keep")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="leaves"):
        resolve_source_path(root, "linked/file")
    with pytest.raises(ValueError, match="leaves"):
        resolve_staging_path(root, "linked/file")
    with pytest.raises(ValueError, match="unsafe"):
        resolve_staging_path(root, "../outside/file")
    assert (outside / "file").read_bytes() == b"keep"


def test_invalid_cache_does_not_send_conditional_headers(tmp_path, signing_key, monkeypatch):
    private, public_path = signing_key
    bundled = tmp_path / "bundled.json"
    bundled.write_bytes(_signed(_payload(1), private))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "manifest-v2.json").write_bytes(b"broken cache")
    (cache_dir / "manifest-v2-meta.json").write_text(
        json.dumps({"etag": '"old"', "url": "https://example.test/manifest.json"}),
        encoding="utf-8",
    )
    initialize_manifest(bundled_path=bundled, public_key_path=public_path, cache_dir=cache_dir)

    def response(request, **_kwargs):
        assert request.get_header("If-none-match") is None
        return _Response(_signed(_payload(2), private))

    monkeypatch.setattr("urllib.request.urlopen", response)
    result = check_remote_manifest(
        url="https://example.test/manifest.json", public_key_path=public_path, cache_dir=cache_dir
    )
    assert result.error is None
    assert result.manifest.revision == 2


@pytest.mark.parametrize("changed", [False, True])
def test_package_status_uses_publication_date(monkeypatch, changed):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from emu68hatcher.gui.tabs import start

    payload = _payload(1790179098)
    payload["catalogs"][0]["revision"] = 2
    manifest = UpdateManifestV2.model_validate(payload)
    selection = ManifestSelection(
        manifest, "remote", changed=changed, checked=True, catalog=manifest.catalogs[0].snapshot()
    )
    tab = SimpleNamespace(
        _update_selection=selection,
        hatcher_update_icon=Mock(),
        hatcher_update_label=Mock(),
        manifest_update_icon=Mock(),
        manifest_update_label=Mock(),
        open_release_btn=Mock(),
        download_update_btn=Mock(),
    )
    monkeypatch.setattr(start, "_set_status_icon", Mock())
    start.StartTab.refresh_update_status(tab)
    prefix = "Package list updated from server" if changed else "Package list is up to date"
    tab.manifest_update_label.setText.assert_called_once_with(
        f"{prefix}: 2026-09-23 (revision 1790179098)"
    )


def test_reset_app_data_refreshes_catalog_consumers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from emu68hatcher.data.package_loader import load_all_packages
    from emu68hatcher.gui.tabs import start

    payload = _payload(100)
    packages = payload["catalogs"][0]["packages"]
    del packages["amelinium"]
    for name, group in (("update_only", "Utilities"), ("update_locale", "Locale")):
        packages[name] = {"name": name, "friendly_name": name, "description": "", "group": group}
    activate_manifest(ManifestSelection(UpdateManifestV2.model_validate(payload), "remote"))

    displayed = {}

    def refresh_consumers():
        current = load_all_packages()
        displayed["software"] = {p.name for p in current if p.group != "Locale"}
        displayed["locales"] = {p.name for p in current if p.group == "Locale"}

    refresh_consumers()
    assert "update_only" in displayed["software"]
    assert "update_locale" in displayed["locales"]
    assert "amelinium" not in displayed["software"]

    box = Mock()
    delete_button = object()
    box.addButton.side_effect = [delete_button, object()]
    box.clickedButton.return_value = delete_button
    monkeypatch.setattr(start, "QMessageBox", Mock(return_value=box))
    monkeypatch.setattr(start, "QApplication", Mock())
    reset_runtime = Mock(return_value=[])
    monkeypatch.setattr("emu68hatcher.utils.paths.reset_runtime_data", reset_runtime)
    monkeypatch.setattr(
        "emu68hatcher.data.update_manifest.initialize_manifest",
        lambda: initialize_manifest(cache_dir=tmp_path / "reset-cache"),
    )
    tab = SimpleNamespace(
        _worker=None,
        _update_worker=None,
        _app_download_worker=None,
        progress_group=Mock(),
        catalog_changed=Mock(),
        refresh_status=Mock(),
        refresh_update_status=Mock(),
    )
    tab.catalog_changed.emit.side_effect = refresh_consumers
    start.StartTab.reset_app_data(tab)

    reset_runtime.assert_called_once_with()
    tab.catalog_changed.emit.assert_called_once_with()
    assert tab._update_selection.source == "bundled"
    assert "amelinium" in displayed["software"]
    assert "update_only" not in displayed["software"]
    assert "update_locale" not in displayed["locales"]
