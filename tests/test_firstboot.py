from types import SimpleNamespace

from emu68hatcher.builder.staging.scripts.injector import apply_package_scripts
from emu68hatcher.data.package_loader import get_local_packages_dir


def test_firstboot_does_not_open_env_as_a_volume():
    startup = get_local_packages_dir() / "System" / "S" / "Startup-Sequence_FirstBoot"
    assert ">ENV:" not in startup.read_text(encoding="iso-8859-1")


def test_cached_font_registration_does_not_return_to_user_startup(tmp_path, monkeypatch):
    from emu68hatcher.data import package_loader

    legacy = SimpleNamespace(name="TrueType fonts")
    other = SimpleNamespace(
        name="Other setup",
        target="S/User-Startup",
        content='Echo "keep this setup"',
        when_user_archive=None,
    )
    package = SimpleNamespace(name="ttflib", scripts=[legacy, other])
    monkeypatch.setattr(package_loader, "get_package_by_name", lambda _name: package)
    assert apply_package_scripts(tmp_path, ["ttflib"], set()) == 1
    startup = (tmp_path / "S/User-Startup").read_text(encoding="iso-8859-1")
    assert "TrueType fonts" not in startup
    assert "keep this setup" in startup
