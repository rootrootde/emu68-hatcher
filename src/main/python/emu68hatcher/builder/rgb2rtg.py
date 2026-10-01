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
from emu68hatcher.config.schema import Emu68Version, Unit0Mode, WorkbenchScreenMode

_CHECKSUMS = {
    "Amiga/RGB2RTG.info": "dd03d5812540909b825397b16ee4a912",
    "Amiga/RGB2RTG/Commands.txt": "063fc4bba3674a03b7f340840988cc7c",
    "Amiga/RGB2RTG/Commands.txt.info": "bd32146bda117699539a7bba2a102c5e",
    "Amiga/RGB2RTG/Files/C/rgb2rtg": "8b7a46b5fb4708041812c3223b7f6d77",
    "Amiga/RGB2RTG/Files/Kernel/Emu68-rgb2rtg.gz": "4369f790dd2ed50bcc9b9fa63e38c152",
    "Amiga/RGB2RTG/Files/Libs/Picasso96/VideoCore.card": "9eafc739ab9ae051656c18ed87f7e875",
    "Amiga/RGB2RTG/Files/Overlays/unicam.dtbo": "8178d6f103b3f45ec0d8d0a9f14da91c",
    "Amiga/RGB2RTG/Install": "d23f3ce0c7efb2143da75102e524fe5d",
    "Amiga/RGB2RTG/Install.info": "79d544d40d5e76b402d0ff48964f129c",
    "Amiga/RGB2RTG/ReadMe.txt": "545ce1e3d747e8472027b4d7abc206d7",
    "Amiga/RGB2RTG/ReadMe.txt.info": "bd32146bda117699539a7bba2a102c5e",
    "Amiga/RGB2RTG/Uninstall": "f18003e7a321f268a1dbdaea5415ae2e",
    "Amiga/RGB2RTG/Uninstall.info": "b08325e3dd8dbb4e591f0d8e08111f81",
    "Commands.txt": "d13354e46c432164bba7bdc324d39a77",
    "Licenses/Capstone-LICENSE.txt": "46aad282a6e300251bde9300e4855bc0",
    "Licenses/Capstone-LICENSE_LLVM.txt": "d240dcec849d48db9ab3d08bf4f08642",
    "Licenses/Emu68-third-party-notices.txt": "b054c6e9efbe49677bb3961bfd7fe0f3",
    "Licenses/GPL-2.0.txt": "ffa10f40b98be2c2bc9608f56827ed23",
    "Licenses/MPL-2.0.txt": "55a288862ec4d1fd20f996344d511a1f",
    "Licenses/UAE-WinUAE-notice.txt": "83f929bde0e5a3235f26e31b81d58716",
    "Licenses/libdeflate-COPYING.txt": "d6cb6968598d941138672496e0afb212",
    "PC/SD-Setup.ps1": "39c770b073b2f96e77ab6ddafd8912c7",
    "README.txt": "c30b81b5dd35ad60fc698428db8d8c4f",
    "SD-Setup.cmd": "143464c2b2f96a36dc0fc57523df4a45",
    "Source/RGB2RTG-0.73-src.zip": "4e8d6809eab0ce0c2a12ebbfbd9f01d7",
}


def extract_rgb2rtg(archive: Path, destination: Path) -> Path:
    result = extract_archive(Path(archive).expanduser(), destination)
    if not result.success:
        raise BuildError(f"Cannot extract RGB2RTG: {result.error}")
    roots = list(destination.rglob("CHECKSUMS.txt"))
    if len(roots) != 1:
        raise BuildError("RGB2RTG archive must contain the v0.73 release")
    root = roots[0].parent
    for relative, digest in _CHECKSUMS.items():
        source = root / relative
        if not source.is_file() or hashlib.md5(source.read_bytes()).hexdigest() != digest:
            raise BuildError(f"RGB2RTG v0.73 file is missing or changed: {relative}")
    return root


def validate_rgb2rtg(workflow) -> None:
    config = workflow.config
    if not config.rgb2rtg.enabled:
        return
    if config.emu68_version != Emu68Version.V1_1_0_BETA_1:
        raise BuildError("RGB2RTG v0.73 requires the Emu68 1.1.0-beta.1 baseline")
    if config.display.workbench_mode == WorkbenchScreenMode.NATIVE:
        raise BuildError("RGB2RTG requires a VideoCore Workbench screen mode")
    if config.emu68_boot.config_txt.framethrower:
        raise BuildError("Disable Framethrower/C790 capture before selecting RGB2RTG")
    if config.emu68_boot.cmdline_txt.emmc_unit0 != Unit0Mode.READ_WRITE:
        raise BuildError("RGB2RTG on Pi 4 requires read/write eMMC unit 0 boot access")
    for line in config.emu68_boot.config_txt.extra_lines:
        key = line.split("=", 1)[0].lower()
        if (
            key.startswith("hdmi_")
            or key in {"max_framebuffer_width", "max_framebuffer_height", "disable_overscan"}
            or (
                key == "dtoverlay"
                and any(token in line.lower() for token in ("unicam", "pal", "ntsc"))
            )
        ):
            raise BuildError(f"Remove the additional RGB2RTG display override: {line}")
    if config.rgb2rtg.archive is None or not config.rgb2rtg.archive.expanduser().is_file():
        raise BuildError("Select the local RGB2RTG_A1200_v0.73.7z release archive")
    workflow._check_cancelled()
    with TemporaryDirectory(prefix="hatcher-rgb2rtg-") as scratch:
        extract_rgb2rtg(config.rgb2rtg.archive, Path(scratch))
    workflow._check_cancelled()


def rgb2rtg_config(text: str, video: str) -> str:
    # runtime video changes insert their block after the global cmdline anchor
    if "# RGB2RTG begin" in text:
        return text
    lines = text.split("\n")
    display = []
    kept = []
    in_hdmi = False
    for line in lines:
        if line == "# HDMI output":
            in_hdmi = True
        elif (
            in_hdmi
            and line.startswith("# ")
            and line
            in {
                "# Emu68 overlays",
                "# Additional settings",
                "# PiStorm model detection: GPIO inputs with pull-ups",
            }
        ):
            in_hdmi = False
        if in_hdmi and (line.startswith("#") or not line):
            continue
        key = line.split("=", 1)[0]
        if key.startswith("hdmi_") or key in {
            "max_framebuffer_width",
            "max_framebuffer_height",
            "disable_overscan",
        }:
            display.append(line)
        else:
            kept.append(line)
    first_filter = 2 if kept[:2] == ["# Generated by Emu68 Hatcher.", ""] else 0
    ntsc = video == "ntsc"
    block = display + [
        "cmdline=cmdline.txt",
        "# RGB2RTG begin",
        "dtoverlay=unicam,boot",
        f"hdmi_group={2 if ntsc else 1}",
        f"hdmi_mode={82 if ntsc else 31}",
        "hdmi_force_mode=1",
        "hdmi_pixel_encoding=2",
        f"dtoverlay={video}",
        "# RGB2RTG end",
        "",
    ]
    kept[first_filter:first_filter] = block
    section = ""
    result = []
    for line in kept:
        if line.startswith("["):
            section = line
        if section in {"[gpio4=0]", "[gpio24=0]"} and line.startswith("kernel="):
            result += [
                "# RGB2RTG: the A1200's own picture on HDMI. Uninstalling it restores the #RGB2RTG-WAS: line.",
                "kernel=kernel/Emu68-rgb2rtg.gz",
                "#RGB2RTG-WAS: " + line,
            ]
        else:
            result.append(line)
    return "\n".join(result)


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
    shutil.copy2(files / "Kernel/Emu68-rgb2rtg.gz", fat / "kernel/Emu68-rgb2rtg.gz")
    (fat / "overlays").mkdir(exist_ok=True)
    shutil.copy2(files / "Overlays/unicam.dtbo", fat / "overlays/unicam.dtbo")
    for name in ("config.txt", "configBAK.txt"):
        path = fat / name
        if path.exists():
            text = path.read_text(encoding="iso-8859-1")
            baseline = fat / (name + ".pre-rgb2rtg")
            if not baseline.exists():
                baseline.write_text(text, encoding="iso-8859-1", newline="\n")
            path.write_text(
                rgb2rtg_config(text, workflow.config.rgb2rtg.video),
                encoding="iso-8859-1",
                newline="\n",
            )
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
            f"video={workflow.config.rgb2rtg.video} picture=on hdmisync=on hdmicolours=full\n",
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
    workflow.logger.info("Installed RGB2RTG v0.73 for A1200/PiStorm32-lite with Pi 4")
