import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from emu68hatcher.builder.ags_profiles import get_profile
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.pipeline.import_ags import stage_import_ags, verify_ags_staging
from emu68hatcher.builder.state import CreatedImage
from emu68hatcher.config.display_models import WorkbenchScreenMode


@pytest.mark.parametrize("profile", ["v30", "v31-beta-160726"])
@pytest.mark.parametrize(
    "workbench_mode", [WorkbenchScreenMode.NATIVE, WorkbenchScreenMode.VIDEOCORE_1280X720]
)
def test_portable_import_repairs_directory_checks_and_preserves_other_scripts(
    tmp_path: Path, monkeypatch, profile, workbench_mode
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
        "AGS2/Themes/My Theme.CONF": b"mode = $29000\n# caf\xe9\n",
        "AGS2/Themes/default.iff": b"unchanged background image",
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
        config=SimpleNamespace(
            boot_device="BOOT", display=SimpleNamespace(workbench_mode=workbench_mode)
        ),
        _cancelled=False,
        _check_cancelled=lambda: None,
        _update_state=lambda *_args, **_kwargs: None,
        _milestone=lambda _message: None,
    )
    copied = []

    def copy_helper(args, log, _cancel):
        if args[:2] == ["fs", "dir"]:
            assert args[2].endswith("/rdb/3/AGS2/Themes")
            entries = [
                {
                    "relativePathComponents": [Path(path).name],
                    "size": len(data),
                    "type": 1,
                    "properties": {"$ProtectionBits": "2", "Comment": ""},
                }
                for path, data in originals.items()
                if path.startswith("AGS2/Themes/")
            ]
            log.write_text(json.dumps({"entries": entries}))
            return
        assert args[:2] == ["fs", "copy"]
        source = args[2]
        if "/AGS2/OS/C/" not in source:
            relative = source.split("/rdb/3/")[1]
            selected = (
                {
                    p: d
                    for p, d in originals.items()
                    if p.lower().endswith(".conf") and "/Themes/" in p
                }
                if relative == "AGS2/Themes/*.conf"
                else {relative: originals[relative]}
            )
            for path, data in selected.items():
                (Path(args[3]) / Path(path).name).write_bytes(data)
            return
        assert "/AGS2/OS/C/" in source
        destination = Path(args[3])
        assert destination == boot / "C"
        copied.append(Path(source).name)
        (destination / Path(source).name).write_bytes(b"missing helper")

    monkeypatch.setattr(import_ags, "validate_source_identity", lambda _inventory: None)
    monkeypatch.setattr(ags_source, "run_hst_to_file", copy_helper)
    from emu68hatcher.builder import ags_display

    monkeypatch.setattr(ags_display, "run_hst_to_file", copy_helper)

    result = stage_import_ags(workflow, image)
    verify_ags_staging(workflow, result)

    assert copied == ["kgiconload", "WBLoad", "WBRun"]
    assert (boot / "C/Ex").read_bytes() == b"existing helper"
    assert (boot / "S/WHDLoad.prefs").read_bytes() == prefs
    assert (boot / "S/WHDLoad-Startup").read_bytes() == startup
    assert not (boot / "S/AGS-Stuff").exists()
    expected = dict(originals)
    if workbench_mode != WorkbenchScreenMode.NATIVE:
        for path in expected:
            if path.lower().endswith(".conf"):
                expected[path] = expected[path].replace(b"$29000", b"$501D1000")
    expected["AGS2/Scripts/Check_Drives"] = (
        b"InfoNew >ENV:GamesDrive Games:\n"
        b"Assign >NIL: EXISTS Emulators:\n"
        b"If NOT WARN\n SetEnv Emulators 1\nEndif\n"
    )
    from emu68hatcher.builder.ags_display import theme_switch_script

    for name, rtg in (("Themes_AGAtoRTG", True), ("Themes_RTGtoAGA", False)):
        expected["AGS2/Scripts/" + name] = theme_switch_script(0x501D1000, rtg=rtg)
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
    assert (
        b'If "$SYSTEM" EQ "PiStorm"\n'
        b" If NOT EXISTS ENV:HW\n"
        b'  SetEnv HW "Real"\n'
        b"  Copy >NIL: ENV:HW ENVARC:HW\n"
        b" EndIf\n"
        b"EndIf\n"
    ) in setup
    assert b"\r" not in setup
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
    for name in ("Themes_AGAtoRTG", "Themes_RTGtoAGA"):
        relative = "AGS2/Scripts/" + name
        hashes[relative] = hashlib.sha256(expected[relative]).hexdigest()
    if workbench_mode != WorkbenchScreenMode.NATIVE:
        for path, data in expected.items():
            if path.lower().endswith(".conf"):
                hashes[path] = hashlib.sha256(data).hexdigest()
    assert {
        (relative, digest)
        for device, relative, digest in result.ags_launcher.verified_files
        if device == "CUSTOM"
    } == set(hashes.items())
    (whd / "AGS2/Scripts/Check_Drives").write_bytes(originals["AGS2/Scripts/Check_Drives"])
    with pytest.raises(BuildError, match="AGS staged portable file differs"):
        verify_ags_staging(workflow, result)
