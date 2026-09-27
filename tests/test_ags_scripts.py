import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from emu68hatcher.builder.ags_profiles import get_profile
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.import_ags import stage_import_ags, verify_ags_staging
from emu68hatcher.builder.state import CreatedImage


@pytest.mark.parametrize("profile", ["v30", "v31-beta-160726"])
def test_portable_import_repairs_directory_checks_and_preserves_other_scripts(
    tmp_path: Path, monkeypatch, profile
):
    from emu68hatcher.builder import ags_source
    from emu68hatcher.builder.pipeline import import_ags

    staging = tmp_path / "staging"
    boot = staging / "BOOT"
    (boot / "S").mkdir(parents=True)
    (boot / "C").mkdir()
    prefs = b"SavePath=SYS:MySaves\nExecuteStartup=S:WHDLoad-Startup\n"
    (boot / "S/WHDLoad.prefs").write_bytes(prefs)
    startup = b"C:NetShutdown\n"
    (boot / "S/WHDLoad-Startup").write_bytes(startup)
    user_startup = b"; existing setup\nAssign MyData: SYS:Data\n"
    (boot / "S/User-Startup").write_bytes(user_startup)
    for name in ("Ex", "WHDLoad", "IconX"):
        (boot / "C" / name).write_bytes(b"existing helper")
    whd = staging / "CUSTOM"
    originals = {
        "AGS2/AGS2.conf": b"mode = $29000\nbackground = AGS:Themes/default.iff\n",
        "AGS2/Themes/default.conf": b"mode = $29000\n",
        "AGS": b"Assign AGS: AGS2\nExecute Scripts:Start_AGS\n",
        "AGS2/Start_AGS": b"Ex Scripts:Start_AGS",
        "AGS2/Scripts/Start_AGS": b"If NOT EXISTS s:AGS-Stuff\n SetEnv Portable 1\nEndIf\n",
        "AGS2/Scripts/Speed_Reset": b"Copy >NIL: Scripts:WHDLoad-Startup S:\n",
        "AGS2/Scripts/Check_Drives": (
            b"InfoNew >ENV:GamesDrive Games:\n"
            b"InfoNew >ENV:EmulatorsDrive Emulators:\n"
            b'Search >NIL: ENV:EmulatorsDrive "Emulators [Mounted]" QUIET\n'
            b"If NOT WARN\n SetEnv Emulators 1\nEndif\n"
        ),
        "AGS2/Scripts/Expert_Boot_Game": b'Echo >ENV:BOOTGAME "$XPath.run"\n',
        "AGS2/+  Extra Games.ags/A.ags/Alley Cat.run": b"Assign SYS: DH0:\n",
    }
    for relative, data in originals.items():
        path = whd / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    roles = ("whdload", "games", "work", "media")
    targets = {role: SimpleNamespace(role=role, device=role.upper()) for role in roles}
    targets["whdload"].device = "CUSTOM"
    plan = SimpleNamespace(
        inventory=SimpleNamespace(
            profile=profile,
            warnings=(),
            source_path=tmp_path / "source.img",
            whdload=SimpleNamespace(index=3),
            source_scripts=originals,
        ),
        script_plan=SimpleNamespace(selected_roles=roles),
        targets=tuple(targets.values()),
        target=targets.__getitem__,
    )
    workspace = SimpleNamespace(validated=SimpleNamespace(ags_plan=plan), staging_dir=staging)
    image = CreatedImage(
        extracted=SimpleNamespace(downloaded=SimpleNamespace(workspace=workspace)),
        image_path=tmp_path / "target.img",
    )
    workflow = SimpleNamespace(
        config=SimpleNamespace(boot_device="BOOT"),
        _cancelled=False,
        _update_state=lambda *_args, **_kwargs: None,
        _milestone=lambda _message: None,
    )
    copied = []

    def copy_helper(args, _log, _cancel):
        assert args[:2] == ["fs", "copy"]
        source = args[2]
        assert "/AGS2/OS/C/" in source
        destination = Path(args[3])
        assert destination == boot / "C"
        copied.append(Path(source).name)
        (destination / Path(source).name).write_bytes(b"missing helper")

    monkeypatch.setattr(import_ags, "validate_source_identity", lambda _inventory: None)
    monkeypatch.setattr(ags_source, "run_hst_to_file", copy_helper)

    result = stage_import_ags(workflow, image)
    verify_ags_staging(workflow, result)

    assert copied == ["kgiconload", "WBLoad", "WBRun"]
    assert (boot / "C/Ex").read_bytes() == b"existing helper"
    assert (boot / "S/WHDLoad.prefs").read_bytes() == prefs
    assert (boot / "S/WHDLoad-Startup").read_bytes() == startup
    assert not (boot / "S/AGS-Stuff").exists()
    expected = dict(originals)
    expected["AGS2/Scripts/Check_Drives"] = (
        b"InfoNew >ENV:GamesDrive Games:\n"
        b"Assign >NIL: EXISTS Emulators:\n"
        b"If NOT WARN\n SetEnv Emulators 1\nEndif\n"
    )
    assert {
        p.relative_to(whd).as_posix(): p.read_bytes() for p in whd.rglob("*") if p.is_file()
    } == expected
    assert {p.name for p in staging.iterdir()} == {"BOOT", "CUSTOM"}
    assert not (boot / "Emu68-Hatcher").exists()
    assert not (boot / "Emu68-Hatcher.info").exists()
    assert not (boot / "C/Hatcher-AGS-ScreenMode").exists()
    setup = (boot / "S/User-Startup").read_bytes()
    assert setup.startswith(user_startup)
    assert b"Hatcher-AGS-ScreenMode" not in setup
    assert b"AGS screen mode" not in setup
    assert b'Assign WHD_Games: "CUSTOM:"' in setup
    assert b'Assign WHD_Demos: "CUSTOM:"' in setup
    assert b'Assign AGS: "CUSTOM:AGS2"' in setup
    assert b'Assign Emulators: "WORK:Emulators"' in setup
    assert b'Assign Premium: "GAMES:Premium"' in setup
    assert b"SetEnv" not in setup
    assert b"WHDSaves" not in setup
    assert b"MakeDir" not in setup
    assert b"Execute" not in setup
    assert b"Quit" not in setup
    assert b"S/AGS-Stuff" not in setup
    result = stage_import_ags(workflow, image)
    verify_ags_staging(workflow, result)
    assert (boot / "S/User-Startup").read_bytes() == setup
    hashes = dict(get_profile(profile).marker_hashes)
    hashes["AGS2/Scripts/Check_Drives"] = hashlib.sha256(
        expected["AGS2/Scripts/Check_Drives"]
    ).hexdigest()
    assert {
        (relative, digest)
        for device, relative, digest in result.ags_launcher.verified_files
        if device == "CUSTOM"
    } == set(hashes.items())
    (whd / "AGS2/Scripts/Check_Drives").write_bytes(originals["AGS2/Scripts/Check_Drives"])
    with pytest.raises(BuildError, match="AGS staged portable file differs"):
        verify_ags_staging(workflow, result)
