import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

from emu68hatcher.builder.pipeline.configure_hardware import _configure_videocore_tooltypes
from emu68hatcher.builder.rgb2rtg import configure_rgb2rtg
from emu68hatcher.builder.staging.files import read_info_tooltypes, write_info_tooltypes


def test_rgb2rtg_preserves_custom_settings_but_replaces_board_identity(tmp_path):
    monitor = tmp_path / "dEvS" / "mOnItOrS" / "vIdEoCoRe.info"
    monitor.parent.mkdir(parents=True)
    source = (
        Path(__file__).parents[1]
        / "src/main/python/emu68hatcher/data/local_packages/System/_tool.info"
    )
    shutil.copy2(source, monitor)
    write_info_tooltypes(
        monitor,
        [
            "boardtype=PicassoIV",
            "SettingsFile=SYS:OldSettings",
            "(VC4_LEGACY_ID)",
            "VC4_LEGACY_ID=No",
            "VC4_SCALER=2",
            "DisplayChain=No",
            "Custom=Keep",
        ],
    )
    workflow = SimpleNamespace(
        config=SimpleNamespace(rgb2rtg=SimpleNamespace(enabled=True)),
        logger=logging.getLogger(__name__),
    )

    _configure_videocore_tooltypes(workflow, tmp_path)

    tooltypes = read_info_tooltypes(monitor)
    assert "BOARDTYPE=Videocore" in tooltypes
    assert "SETTINGSFILE=SYS:DEVS/Picasso96Settings" in tooltypes
    assert "VC4_LEGACY_ID" in tooltypes
    assert "boardtype=PicassoIV" not in tooltypes
    assert "SettingsFile=SYS:OldSettings" not in tooltypes
    assert "VC4_LEGACY_ID=No" not in tooltypes
    assert "(VC4_LEGACY_ID)" not in tooltypes
    assert "VC4_SCALER=2" in tooltypes
    assert "VC4_SCALER=3" not in tooltypes
    assert "DisplayChain=No" in tooltypes
    assert "Custom=Keep" in tooltypes


def test_rgb2rtg_installs_with_picture_off_and_keeps_saved_preferences(tmp_path):
    source = tmp_path / "release"
    staging = tmp_path / "staging"
    boot = staging / "SDH0"
    fat = staging / "EMU68BOOT"
    for path in (
        source / "Amiga/RGB2RTG/Files/Libs/Picasso96/VideoCore.card",
        source / "Amiga/RGB2RTG/Files/C/rgb2rtg",
        source / "Amiga/RGB2RTG/Files/Kernel/Emu68-rgb2rtg.gz",
        source / "Amiga/RGB2RTG/Files/Overlays/unicam.dtbo",
        source / "Amiga/RGB2RTG/ReadMe.txt",
        source / "Amiga/RGB2RTG/Commands.txt",
        source / "Licenses/license.txt",
        boot / "Libs/Picasso96/VideoCore.card",
        boot / "S/User-Startup",
        fat / "overlays/pal.dtbo",
        fat / "overlays/ntsc.dtbo",
        fat / "Emu68-pistorm.gz",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"baseline\n")
    (fat / "config.txt").write_bytes(b"[gpio24=0]\nkernel=Emu68-pistorm.gz\n")
    (boot / "Prefs/Env-Archive").mkdir(parents=True)
    monitor = boot / "Devs/Monitors/Videocore.info"
    monitor.parent.mkdir(parents=True)
    shutil.copy2(
        Path(__file__).parents[1]
        / "src/main/python/emu68hatcher/data/local_packages/System/_tool.info",
        monitor,
    )
    workflow = SimpleNamespace(
        config=SimpleNamespace(rgb2rtg=SimpleNamespace(enabled=True, video="pal")),
        logger=logging.getLogger(__name__),
        _check_cancelled=lambda: None,
    )
    image = SimpleNamespace(
        workspace=SimpleNamespace(staging_dir=staging),
        extracted=SimpleNamespace(extracted_paths={"rgb2rtg": source}),
    )

    configure_rgb2rtg(workflow, image, boot)

    prefs = boot / "Prefs/Env-Archive/RGB2RTG.prefs"
    assert prefs.read_bytes() == b"video=pal picture=off hdmisync=on hdmicolours=full\n"
    assert (boot / "C/rgb2rtg").is_file()
    assert b"kernel=kernel/Emu68-rgb2rtg.gz" in (fat / "config.txt").read_bytes()
    assert "DisplayChain=Yes" in read_info_tooltypes(monitor)
    assert b"C:rgb2rtg APPLY >NIL:" in (boot / "S/User-Startup").read_bytes()

    saved = b"video=pal picture=on look=smooth hdmisync=off\n"
    prefs.write_bytes(saved)
    configure_rgb2rtg(workflow, image, boot)
    assert prefs.read_bytes() == saved
    assert (boot / "S/User-Startup").read_bytes().count(b"C:rgb2rtg APPLY >NIL:") == 1
