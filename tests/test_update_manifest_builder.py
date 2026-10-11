import importlib.util
import json
from pathlib import Path

import pytest
from emu68hatcher.data.catalog import load_catalog_source
from emu68hatcher.data.update_manifest import UpdateManifestV1


def _load_builder_module():
    path = Path(__file__).parents[1] / "scripts" / "build-update-manifest.py"
    spec = importlib.util.spec_from_file_location("update_manifest_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_assets_are_mapped_by_platform(tmp_path):
    builder = _load_builder_module()
    filename = "emu68hatcher-1.0.0-macos-arm64.dmg"
    artifact = tmp_path / filename
    artifact.write_bytes(b"installer")
    release = {
        "tag_name": "v1.0.0",
        "html_url": "https://github.com/rootrootde/emu68hatcher/releases/tag/v1.0.0",
        "assets": [
            {
                "name": filename,
                "browser_download_url": (
                    "https://github.com/rootrootde/emu68hatcher/releases/download/"
                    f"v1.0.0/{filename}"
                ),
            }
        ],
    }

    payload = builder._release_payload(release, tmp_path)
    assert payload["version"] == "1.0.0"
    assert payload["artifacts"]["macos-arm64"]["filename"] == filename
    assert payload["artifacts"]["macos-arm64"]["size"] == 9


def test_legacy_hash_refresh_preserves_other_download_fields():
    builder = _load_builder_module()
    root = Path(__file__).parents[1]
    legacy = UpdateManifestV1.model_validate(
        json.loads((root / "updates/legacy-manifest-source.json").read_text())
    )
    catalog = load_catalog_source()

    updated = builder.refresh_legacy_hashes(legacy, catalog, ["amelinium"])
    assert updated.packages["amelinium"].hash == catalog.packages["amelinium"].download.hash
    assert updated.packages["amelinium"].url == legacy.packages["amelinium"].url
    assert updated.packages["amigaamp"] == legacy.packages["amigaamp"]

    catalog.packages["amelinium"].download.url = "https://example.org/other.lha"
    with pytest.raises(ValueError, match="legacy download source changed"):
        builder.refresh_legacy_hashes(legacy, catalog, ["amelinium"])
