import re

import pytest
from emu68hatcher.builder.staging.scripts.injector import (
    STARTUP_SEQUENCE_INJECTIONS,
    apply_standard_injections,
    inject_script,
)
from emu68hatcher.data.package_loader import get_local_packages_dir

CLOANTO_SETPATCH = """FailAt 21
C:Version >NIL: exec.library 45 20
If WARN
  C:SetPatch QUIET
Else
  C:Version >NIL: C:SetPatch 44 16
  If WARN
    C:SetPatch QUIET
  Else
    C:SetPatch NOROMUPDATE QUIET
  EndIf
EndIf
"""
INLINE_MONITORS = """IF EXISTS DEVS:Monitors
  IF EXISTS DEVS:Monitors/VGAOnly
    DEVS:Monitors/VGAOnly
  EndIF
  C:List >NIL: DEVS:Monitors/~(#?.info|VGAOnly) TO T:M LFORMAT "DEVS:Monitors/%s"
  Execute T:M
  C:Delete >NIL: T:M
EndIF
"""


@pytest.mark.parametrize(
    "setpatch,monitors,mount,use_remlib",
    [
        (CLOANTO_SETPATCH, INLINE_MONITORS, "C:Mount >NIL: DEVS:DOSDrivers/~(#?.info)", True),
        ("C:SetPatch QUIET\n", INLINE_MONITORS, "C:Mount DEVS:DOSDrivers/~(#?.info)", True),
        ("SetPatch QUIET\n", "C:LoadMonDrvs\n", "Mount DEVS:DOSDrivers/~(#?.info)", True),
        ("C:SetPatch QUIET\n", INLINE_MONITORS, "C:Mount DEVS:DOSDrivers/~(#?.info)", False),
    ],
)
def test_startup_variants_keep_setpatch_branches_and_monitor_order(
    tmp_path, setpatch, monitors, mount, use_remlib
):
    startup = tmp_path / "S/Startup-Sequence"
    startup.parent.mkdir()
    startup.write_bytes(
        (setpatch + "BindDrivers\n" + mount + "\n" + monitors + "C:IPrefs\nC:LoadWB\n").encode(
            "iso-8859-1"
        )
    )
    content_base = get_local_packages_dir() / "System"
    results = apply_standard_injections(tmp_path, content_base, use_remlib=use_remlib)
    assert not [result.error for result in results if result.error]
    result = startup.read_text(encoding="iso-8859-1")
    assert monitors in result
    if setpatch == CLOANTO_SETPATCH:
        conditional = setpatch.split("\n", 1)[1]
        assert conditional in result
        assert result.index("C:RemLib") < result.index(conditional)
    elif use_remlib:
        assert result.index("C:RemLib") < result.index(setpatch)
    else:
        assert "C:RemLib" not in result
    mounts = re.findall(r"^\s*(?:C:)?Mount[^\n]*DEVS:DOSDrivers[^\n]*", result, re.M | re.I)
    assert len(mounts) == 1
    assert ">NIL:" in mounts[0]
    assert result.index("BindDrivers") < result.index(";RTC Load - Added")
    assert result.index(";RTC Load - Added") < result.index(";RexxMast - Added")
    assert result.index(";RexxMast - Added") < result.index(";FirstBoot Section - Added")
    assert result.index(";FirstBoot Section - Added") < result.index(";UAEGFX Monitor Swap - Added")
    assert result.index(";UAEGFX Monitor Swap - Added") < result.index(mounts[0])
    assert result.index(mounts[0]) < result.index(monitors) < result.index("C:IPrefs")
    before = startup.read_bytes()
    repeated = [
        inject_script(startup, injection, content_base)
        for injection in STARTUP_SEQUENCE_INJECTIONS
        if injection.name == "Mount DOSDrivers (with >NIL:)"
        or (injection.name == "Iconlib" and use_remlib)
    ]
    assert not [result.error for result in repeated if result.error]
    assert startup.read_bytes() == before
    assert b"\r" not in before


def test_missing_mount_command_still_fails(tmp_path):
    startup = tmp_path / "S/Startup-Sequence"
    startup.parent.mkdir()
    startup.write_bytes(b"C:SetPatch QUIET\nBindDrivers\nC:LoadMonDrvs\nC:IPrefs\n")
    results = apply_standard_injections(tmp_path, get_local_packages_dir() / "System")
    errors = [result.error for result in results if result.error]
    assert len(errors) == 1
    assert "Required pattern not found" in errors[0]
    assert "Mount" in errors[0]
