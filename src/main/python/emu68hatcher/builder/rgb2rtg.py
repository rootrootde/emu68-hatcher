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
    "Amiga/RGB2RTG/Commands.txt": "d0373cd93ebd5be5d1a39bea604a3540",
    "Amiga/RGB2RTG/Commands.txt.info": "bd32146bda117699539a7bba2a102c5e",
    "Amiga/RGB2RTG/Files/C/rgb2rtg": "c93c571b54b8c2234b023529bd1b1d55",
    "Amiga/RGB2RTG/Files/Kernel/Pi3/Emu68-rgb2rtg.gz": "f74cc01b0619286cf09711b7c6f55777",
    "Amiga/RGB2RTG/Files/Kernel/Pi4/Emu68-rgb2rtg.gz": "73002badb7e82e4c883c3dea2937f388",
    "Amiga/RGB2RTG/Files/Libs/Picasso96/VideoCore.card": "9c2395a8aa3f4d0014c65c49dfddc829",
    "Amiga/RGB2RTG/Files/Overlays/sdhc.dtbo": "6b8d01c1c19a169a3678d6a00c84cfa0",
    "Amiga/RGB2RTG/Files/Overlays/unicam.dtbo": "8178d6f103b3f45ec0d8d0a9f14da91c",
    "Amiga/RGB2RTG/Install": "e9bb0fb1924e3359a05e66c4a07e2b55",
    "Amiga/RGB2RTG/Install.info": "79d544d40d5e76b402d0ff48964f129c",
    "Amiga/RGB2RTG/ReadMe.txt": "f5f2c52e0b8282f51c4e7dbdee124003",
    "Amiga/RGB2RTG/ReadMe.txt.info": "bd32146bda117699539a7bba2a102c5e",
    "Amiga/RGB2RTG/Uninstall": "ebc60fcc88c002f725ec63711ec02716",
    "Amiga/RGB2RTG/Uninstall.info": "b08325e3dd8dbb4e591f0d8e08111f81",
    "Commands.txt": "f39d902fa619b9e103f7c0c4a99ae7ab",
    "Licenses/Capstone-LICENSE.txt": "46aad282a6e300251bde9300e4855bc0",
    "Licenses/Capstone-LICENSE_LLVM.txt": "d240dcec849d48db9ab3d08bf4f08642",
    "Licenses/Emu68-third-party-notices.txt": "b054c6e9efbe49677bb3961bfd7fe0f3",
    "Licenses/GPL-2.0.txt": "ffa10f40b98be2c2bc9608f56827ed23",
    "Licenses/MPL-2.0.txt": "55a288862ec4d1fd20f996344d511a1f",
    "Licenses/UAE-WinUAE-notice.txt": "26f4d16ee5376f6e660d418bd5ec93c1",
    "Licenses/libdeflate-COPYING.txt": "d6cb6968598d941138672496e0afb212",
    "PC/SD-Setup.ps1": "fb069c567c28b4ed056c3ebf29616130",
    "README.txt": "60ee9735a408bb531645db70e5917dd3",
    "SD-Setup.cmd": "143464c2b2f96a36dc0fc57523df4a45",
    "Source/RGB2RTG-0.75-src.zip": "589bd0ec940977e36988f5d5befd202a",
}


def extract_rgb2rtg(archive: Path, destination: Path) -> Path:
    result = extract_archive(Path(archive).expanduser(), destination)
    if not result.success:
        raise BuildError(f"Cannot extract RGB2RTG: {result.error}")
    roots = list(destination.rglob("CHECKSUMS.txt"))
    if len(roots) != 1:
        raise BuildError("RGB2RTG archive must contain the v0.75 release")
    root = roots[0].parent
    for relative, digest in _CHECKSUMS.items():
        source = root / relative
        if not source.is_file() or hashlib.md5(source.read_bytes()).hexdigest() != digest:
            raise BuildError(f"RGB2RTG v0.75 file is missing or changed: {relative}")
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
    workflow.logger.info("Installed RGB2RTG v0.75 for A1200/PiStorm32-lite with Pi 4")
