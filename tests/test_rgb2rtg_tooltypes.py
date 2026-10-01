import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

from emu68hatcher.builder.pipeline.configure_hardware import _configure_videocore_tooltypes
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
