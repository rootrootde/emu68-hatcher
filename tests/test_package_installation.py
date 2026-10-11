from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from emu68hatcher.builder.pipeline.extract import _extract_downloaded
from emu68hatcher.builder.pipeline.install_packages import stage_install_packages
from emu68hatcher.builder.staging.packages import PackageInstaller
from emu68hatcher.data.package_schema import InstallRule


def test_install_stage_accepts_registered_download_cache_link(tmp_path, monkeypatch):
    cache = tmp_path / "downloads" / "extracted" / "additem"
    (cache / "AddItem").mkdir(parents=True)
    (cache / "AddItem" / "AddItem").write_bytes(b"executable")
    (cache / "AddItem" / "AddItem.info").write_bytes(b"icon")
    workspace = SimpleNamespace(
        extracted_dir=tmp_path / "extracted", staging_dir=tmp_path / "staging"
    )
    downloaded = SimpleNamespace(
        workspace=workspace,
        downloaded_files={"additem": tmp_path / "additem.lha"},
        required_artifacts={"additem"},
        required_packages={"additem"},
    )
    paths = {"additem": cache}
    workflow = Mock()
    workflow.config.boot_device = "SDH0"
    _extract_downloaded(workflow, downloaded, paths)
    assert (workspace.extracted_dir / "additem").is_dir()
    image = SimpleNamespace(extracted=SimpleNamespace(downloaded=downloaded, extracted_paths=paths))
    monkeypatch.setattr(
        "emu68hatcher.builder.pipeline._selection.get_resolution",
        lambda workflow: SimpleNamespace(
            install_order=["additem"], selected={"additem"}, unsatisfiable={}, dropped={}
        ),
    )
    assert stage_install_packages(workflow, image) is image
    target = workspace.staging_dir / "SDH0" / "WBStartup"
    assert (target / "AddItem").read_bytes() == b"executable"
    assert (target / "AddItem.info").read_bytes() == b"icon"


def test_registered_cache_root_does_not_allow_links_outside_package(tmp_path):
    cache = tmp_path / "cache" / "additem"
    cache.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_bytes(b"outside")
    (cache / "escaped").symlink_to(outside, target_is_directory=True)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    source = extracted / "additem"
    source.symlink_to(cache, target_is_directory=True)
    installer = PackageInstaller(
        tmp_path / "staging", extracted, extracted_paths={"additem": cache}
    )
    with pytest.raises(ValueError, match="leaves"):
        installer._apply_install_rule(
            InstallRule.model_validate({"from": "escaped/file", "to": "C"}), source
        )
    assert not (tmp_path / "staging").exists()


def test_unregistered_external_package_root_is_rejected(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    source = extracted / "additem"
    source.symlink_to(cache, target_is_directory=True)
    installer = PackageInstaller(tmp_path / "staging", extracted)
    with pytest.raises(ValueError, match="package source leaves"):
        installer._apply_install_rule(
            InstallRule.model_validate({"from": "file", "to": "C"}), source
        )
