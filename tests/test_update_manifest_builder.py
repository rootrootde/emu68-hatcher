import importlib.util
from pathlib import Path


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
