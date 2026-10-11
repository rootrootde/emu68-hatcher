import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from emu68hatcher.builder.ags_display import set_theme_mode, theme_switch_script
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.import_ags import verify_ags_target
from emu68hatcher.data.picasso96 import clut_mode_ids, load_default_settings


@pytest.mark.parametrize("old_mode", [b"$29000", b"$00029000", b"$501D1000"])
def test_theme_mode_preserves_layout_and_latin1(old_mode):
    original = b"# caf\xe9\n mode = " + old_mode + b" # PAL\nmenu_x = 37\n"
    assert set_theme_mode(original, 0x501D1000) == (
        b"# caf\xe9\n mode = $501D1000 # PAL\nmenu_x = 37\n"
    )


@pytest.mark.parametrize("data", [b"", b"mode = invalid\n", b"mode=$29000\nmode=$29000\n"])
def test_missing_or_ambiguous_theme_mode_fails(data):
    with pytest.raises(BuildError, match="exactly one"):
        set_theme_mode(data, 0x501D1000)


def test_theme_switch_does_not_require_pistorm_variable_or_menu_entries():
    mode = clut_mode_ids(load_default_settings())[640, 256]
    assert mode == 0x501D1000
    for rtg in (True, False):
        script = theme_switch_script(mode, rtg=rtg).decode("latin1")
        assert "$PiStorm" not in script
        assert "$00029004" not in script
        assert "$500A1000" not in script
        assert "\r" not in script
        for name in ("RTG", "AGA"):
            assert f'If EXISTS "AGS:Themes.ags/- Use {name} Screen -.' in script
            assert f' If NOT EXISTS "AGS:Themes.ags/- Use {name} Screen -.' in script
        assert (
            'SEARCH "$29000" REPLACE "$501D1000"' in script
            if rtg
            else ('SEARCH "$501D1000" REPLACE "$29000"' in script)
        )


@pytest.mark.parametrize("bad_file", [None, "missing", "changed"])
def test_target_verification_reads_theme_configs_together(tmp_path, monkeypatch, bad_file):
    from emu68hatcher.builder import ags_blocks
    from emu68hatcher.builder.host import hst_runner
    from emu68hatcher.builder.pipeline import finalize

    data = b"mode = $501D1000\n"
    digest = hashlib.sha256(data).hexdigest()
    files = (
        ("CUSTOM", "AGS2/Themes/default.conf", digest),
        ("CUSTOM", "AGS2/Themes/Blue.conf", digest),
    )
    image = SimpleNamespace(
        workspace=SimpleNamespace(validated=SimpleNamespace(ags_plan=object())),
        image_path=tmp_path / "target.img",
        ags_launcher=SimpleNamespace(verified_files=files),
    )
    workflow = SimpleNamespace(
        config=SimpleNamespace(boot_device="BOOT"),
        state=SimpleNamespace(elevation="token"),
        _cancelled=False,
        _check_cancelled=lambda: None,
    )
    copied = []

    def run(_self, command, *, elevation):
        assert elevation == "token"
        copied.append(command.args[0])
        destination = Path(command.args[1])
        (destination / "default.conf").write_bytes(data)
        if bad_file != "missing":
            (destination / "Blue.conf").write_bytes(b"mode = $29000\n" if bad_file else data)
        return SimpleNamespace(success=True)

    monkeypatch.setattr(ags_blocks, "verify_target_partitions", lambda *_: None)
    monkeypatch.setattr(finalize, "_build_device_map", lambda _: {"CUSTOM": 2})
    monkeypatch.setattr(hst_runner.HSTRunner, "run_command", run)
    if bad_file:
        with pytest.raises(BuildError, match="AGS target portable file differs.*Blue.conf"):
            verify_ags_target(workflow, image)
    else:
        verify_ags_target(workflow, image)
    assert len(copied) == 1
    assert copied[0].endswith("/mbr/2/rdb/CUSTOM/AGS2/Themes/*.conf")
