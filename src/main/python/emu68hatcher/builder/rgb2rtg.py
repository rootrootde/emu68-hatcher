"""Optional RGB2RTG installation."""

import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.host.archive import extract_archive
from emu68hatcher.builder.staging.files import (
    read_info_tooltypes,
    resolve_source_path,
    resolve_staging_path,
    write_info_tooltypes,
)

_CHECKSUMS = {
    "Amiga/RGB2RTG.info": "dd03d5812540909b825397b16ee4a912",
    "Amiga/RGB2RTG/Commands.txt": "eb9b155e7a1e07f5f9e356624f756173",
    "Amiga/RGB2RTG/Commands.txt.info": "bd32146bda117699539a7bba2a102c5e",
    "Amiga/RGB2RTG/Files/AHI/RGB2RTG-HDMI": "afcd7e3eb05b145ab1eee0560b9d9129",
    "Amiga/RGB2RTG/Files/AHI/rgb2rtg-hdmi.audio": "b8ffe3bf065f773099a1e1e1de572e77",
    "Amiga/RGB2RTG/Files/C/rgb2rtg": "2173877e07b01aada45191c0b94b0894",
    "Amiga/RGB2RTG/Files/Kernel/Pi3/Emu68-rgb2rtg.gz": "7a7a1b46419d9bfd3bdc3eb422a08976",
    "Amiga/RGB2RTG/Files/Kernel/Pi4/Emu68-rgb2rtg.gz": "ac58427a7f3693db22d4971a060b2184",
    "Amiga/RGB2RTG/Files/Libs/Picasso96/VideoCore.card": "39e606391417acc6c868329b09fd5f64",
    "Amiga/RGB2RTG/Files/Overlays/sdhc.dtbo": "6b8d01c1c19a169a3678d6a00c84cfa0",
    "Amiga/RGB2RTG/Files/Overlays/unicam.dtbo": "8178d6f103b3f45ec0d8d0a9f14da91c",
    "Amiga/RGB2RTG/Install": "590e7b77e7137e23c3634782f2d8dc2a",
    "Amiga/RGB2RTG/Install.info": "d42f930ad9bdd6f7a71feddee01f093a",
    "Amiga/RGB2RTG/ReadMe.txt": "2331416ce28f1ef84c0534b0a65d6f82",
    "Amiga/RGB2RTG/ReadMe.txt.info": "bd32146bda117699539a7bba2a102c5e",
    "Amiga/RGB2RTG/Uninstall": "85465b50f2aa93bbf66bb9fe07549e7f",
    "Amiga/RGB2RTG/Uninstall.info": "3e4973afb5f551ea3cc69238020acd86",
    "Commands.txt": "c873e222e5f3e86245033ed982f25519",
    "Licenses/Capstone-LICENSE.txt": "46aad282a6e300251bde9300e4855bc0",
    "Licenses/Capstone-LICENSE_LLVM.txt": "d240dcec849d48db9ab3d08bf4f08642",
    "Licenses/Emu68-third-party-notices.txt": "b054c6e9efbe49677bb3961bfd7fe0f3",
    "Licenses/GPL-2.0.txt": "ffa10f40b98be2c2bc9608f56827ed23",
    "Licenses/MPL-2.0.txt": "55a288862ec4d1fd20f996344d511a1f",
    "Licenses/UAE-WinUAE-notice.txt": "1519498075de67f4dd91027e865a1aae",
    "Licenses/libdeflate-COPYING.txt": "d6cb6968598d941138672496e0afb212",
    "PC/SD-Setup.ps1": "8e2e48e559af5e8a5dc1b71daa34db18",
    "README.txt": "34a5e29bf6c19c7ce9a81935574bf061",
    "SD-Setup.cmd": "143464c2b2f96a36dc0fc57523df4a45",
    "Source/RGB2RTG-0.76-src.zip": "6a29a8c113bb8d9f89e8108bca7d1e87",
}


def extract_rgb2rtg(archive: Path, destination: Path) -> Path:
    result = extract_archive(Path(archive).expanduser(), destination)
    if not result.success:
        raise BuildError(f"Cannot extract RGB2RTG: {result.error}")
    roots = list(destination.rglob("CHECKSUMS.txt"))
    if len(roots) != 1:
        raise BuildError("RGB2RTG archive must contain the v0.76 release")
    root = roots[0].parent
    for relative, digest in _CHECKSUMS.items():
        source = root / relative
        if not source.is_file() or hashlib.md5(source.read_bytes()).hexdigest() != digest:
            raise BuildError(f"RGB2RTG v0.76 file is missing or changed: {relative}")
    return root


def validate_rgb2rtg(workflow) -> None:
    from emu68hatcher.builder.pipeline._selection import rgb2rtg_problems

    config = workflow.config
    if not config.rgb2rtg.enabled:
        return
    problems = rgb2rtg_problems(
        config.emu68_version.value,
        config.display.workbench_mode.value,
        config.emu68_boot.config_txt.framethrower,
        config.emu68_boot.cmdline_txt.emmc_unit0.value,
        config.emu68_boot.config_txt.extra_lines,
        config.rgb2rtg.archive,
    )
    if problems:
        raise BuildError(problems[0][1])
    workflow._check_cancelled()
    with TemporaryDirectory(prefix="hatcher-rgb2rtg-") as scratch:
        extract_rgb2rtg(config.rgb2rtg.archive, Path(scratch))
    workflow._check_cancelled()


def configure_rgb2rtg(workflow, image, boot: Path) -> None:
    source = image.extracted.extracted_paths["rgb2rtg"]
    workflow._check_cancelled()
    fat = image.workspace.staging_dir / "EMU68BOOT"
    files = source / "Amiga/RGB2RTG/Files"
    for overlay in ("pal", "ntsc"):
        if not (fat / "overlays" / f"{overlay}.dtbo").is_file():
            raise BuildError(f"RGB2RTG requires the baseline {overlay}.dtbo overlay")
    recovery = resolve_staging_path(boot, "Emu68-Hatcher/RGB2RTG")
    if not (fat / "config.txt").is_file():
        raise BuildError("RGB2RTG baseline config.txt is missing")
    if "# RGB2RTG begin" not in (fat / "config.txt").read_text(encoding="iso-8859-1"):
        raise BuildError("RGB2RTG baseline config.txt was generated without the RGB2RTG block")
    if not (fat / "Emu68-pistorm.gz").is_file():
        raise BuildError("RGB2RTG baseline PiStorm32-lite kernel is missing")
    recovery.mkdir(parents=True, exist_ok=True)
    card = resolve_source_path(boot, "Libs/Picasso96/VideoCore.card")
    if card is None:
        raise BuildError("RGB2RTG requires the installed VideoCore.card baseline")
    if not (recovery / "VideoCore.card").exists():
        shutil.copy2(card, recovery / "VideoCore.card")
    shutil.copy2(files / "Libs/Picasso96/VideoCore.card", card)
    command = resolve_staging_path(boot, "C/rgb2rtg")
    command.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(files / "C/rgb2rtg", command)
    (fat / "kernel").mkdir(exist_ok=True)
    shutil.copy2(files / "Kernel/Pi4/Emu68-rgb2rtg.gz", fat / "kernel/Emu68-rgb2rtg.gz")
    (fat / "overlays").mkdir(exist_ok=True)
    shutil.copy2(files / "Overlays/unicam.dtbo", fat / "overlays/unicam.dtbo")
    # the HDMI driver for AHI goes in only where the AHI package staged Devs/AHI, as upstream
    ahi = resolve_source_path(boot, "Devs/AHI")
    if ahi is not None and ahi.is_dir():
        shutil.copy2(files / "AHI/rgb2rtg-hdmi.audio", ahi / "rgb2rtg-hdmi.audio")
        modes = resolve_staging_path(boot, "Devs/AudioModes")
        modes.mkdir(parents=True, exist_ok=True)
        shutil.copy2(files / "AHI/RGB2RTG-HDMI", modes / "RGB2RTG-HDMI")
        workflow.logger.info("RGB2RTG: installed the HDMI driver for AHI")
    monitor = resolve_source_path(boot, "Devs/Monitors/Videocore.info")
    if monitor is None:
        raise BuildError("RGB2RTG requires the active VideoCore monitor icon")
    if not (recovery / "Videocore.info").exists():
        shutil.copy2(monitor, recovery / "Videocore.info")
    tooltypes = read_info_tooltypes(monitor)
    write_info_tooltypes(
        monitor,
        [entry for entry in tooltypes if not entry.lower().startswith("displaychain=")]
        + ["DisplayChain=Yes"],
    )
    prefs = resolve_staging_path(boot, "Prefs/Env-Archive/RGB2RTG.prefs")
    if not prefs.exists():
        prefs.write_text(
            f"video={workflow.config.rgb2rtg.video} picture=off hdmisync=on hdmicolours=full\n",
            encoding="iso-8859-1",
            newline="\n",
        )
    startup = resolve_staging_path(boot, "S/User-Startup")
    text = startup.read_text(encoding="iso-8859-1")
    if not (recovery / "User-Startup.backup").exists():
        (recovery / "User-Startup.backup").write_text(text, encoding="iso-8859-1", newline="\n")
    marker = ";RGB2RTG - Added by Emu68 Hatcher - BEGIN"
    if marker not in text:
        startup.write_text(
            text.rstrip("\n")
            + "\n"
            + marker
            + '\nIF "$System" EQ "PiStorm"\n  IF EXISTS C:rgb2rtg\n    C:rgb2rtg APPLY >NIL:\n  ENDIF\nENDIF\n;RGB2RTG - Added by Emu68 Hatcher - END\n',
            encoding="iso-8859-1",
            newline="\n",
        )
    for name in ("ReadMe.txt", "Commands.txt"):
        shutil.copy2(source / "Amiga/RGB2RTG" / name, recovery / name)
    shutil.copytree(source / "Licenses", recovery / "Licenses", dirs_exist_ok=True)
    asset = Path(__file__).parent.parent / "data/rgb2rtg"
    for name in ("Recover", "RemoveMenus.rexx", "Hatcher.txt"):
        shutil.copy2(asset / name, recovery / name)
    workflow.logger.info("Installed RGB2RTG v0.76 for A1200/PiStorm32-lite with Pi 4")
