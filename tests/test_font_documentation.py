from types import SimpleNamespace

from emu68hatcher.builder.staging import packages
from emu68hatcher.data.package_schema import InstallRule


def test_cached_font_rules_install_fonts_without_documentation(tmp_path, monkeypatch):
    extracted = tmp_path / "extracted"
    source = extracted / "fonts_vera"
    source.mkdir(parents=True)
    (source / "Vera.ttf").write_bytes(b"font data")
    (source / "COPYRIGHT.TXT").write_text("font license")
    package = SimpleNamespace(
        install=[
            InstallRule.model_validate({"from": "*.ttf", "to": "Fonts/TrueType/"}),
            InstallRule.model_validate(
                {"from": "COPYRIGHT.TXT", "to": "Emu68-Hatcher/Fonts/BitstreamVera/"}
            ),
            InstallRule.model_validate({"from": "COPYRIGHT.TXT", "to": "EMU68-HATCHER/FONTS/"}),
        ]
    )
    monkeypatch.setattr(packages, "get_package_by_name", lambda _name: package)
    staging = tmp_path / "staging"
    installer = packages.PackageInstaller(staging, extracted, boot_device="BOOT")
    monkeypatch.setattr(installer, "_get_source_dir", lambda _pkg: source)

    assert installer.install_package("fonts_vera") == 1
    assert (staging / "BOOT/Fonts/TrueType/Vera.ttf").read_bytes() == b"font data"
    assert not (staging / "BOOT/Emu68-Hatcher").exists()
