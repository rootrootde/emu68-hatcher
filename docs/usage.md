# Usage

## Build an image

1. **Launch the app.** It checks tools, updates and the package list.

2. **Start tab.** Download missing or outdated tools. App updates go to Downloads and can be opened after verification; on Linux this needs a Debian-based system.

    [![Start tab with installed tools and update status](assets/screenshot_macos.png)](assets/screenshot_macos.png){ target="_blank" }

3. **Amiga Files tab.** Add folders with a Kickstart ROM and Workbench ADFs, then pick version, icon set and languages. Files are matched by hash; **Show details...** lists what was found. AmigaOS 3.9 uses the CD **.iso** and a Kickstart 3.1 ROM instead. GlowIcons needs its matching ADF.

    [![Amiga Files tab with a detected Workbench 3.2.3 set](assets/screenshots/amiga-files.png)](assets/screenshots/amiga-files.png){ target="_blank" }

4. **Emu68 tab.** Pick 1.0.7 stable or 1.1.0-beta.1 and set boot options. The right side previews **config.txt** and **cmdline.txt**; more settings are under **Show Advanced**.

    [![Emu68 tab with release selection and generated boot files](assets/screenshots/emu68.png)](assets/screenshots/emu68.png){ target="_blank" }

5. **Display tab.** Set HDMI and Workbench modes, Picasso96 and native-video capture. Select **Framethrower** or **C790 (HDMI to CSI)** as the capture device. C790 requires Emu68 1.1 or later and a VideoCore Workbench mode. Use **Browse...** for your own full **Picasso96.lha**.

    [![Display tab with HDMI, Workbench and Framethrower settings](assets/screenshots/display.png)](assets/screenshots/display.png){ target="_blank" }

6. **Software tab.** Select optional software and hardware drivers. The list is filtered for the chosen Workbench and Emu68 versions. Required dependencies show which package needs them; libraries are grouped in a collapsed section. MUI is optional unless an application needs it. See [Packages](packages.md) for all packages.

    **Minimal** clears optional software and drivers and sets the Network tab to None. The OS, RTG, filesystem and FirstBoot helpers remain installed, including fat95 for access to the boot partition. ROM, display mode, partitions, icons and install media stay unchanged. Extra content is still copied, so those folders can add software to a minimal image.

    **Select None** clears only software choices; a selected network stack can still require drivers and libraries. **Defaults** restores the standard software selection without changing the Network tab. Save Config stores your choices separately from automatically required packages.

    Emu68 tools and drivers can be selected separately: Ethernet, WiFi, USB, NVMe, I2C clock support and diagnostic tools. A selected network stack includes both Ethernet and WiFi support. USB and NVMe components are offered only for compatible Emu68 versions. The Minimal selection retains RTG support; it is not a native-only installation.

    [![Software tab with package groups](assets/screenshots/software.png)](assets/screenshots/software.png){ target="_blank" }

    The **Fonts** group has four optional packs: Workbench (Apparent, Dina, Terminus), MagicWB (XEN, XHelvetica, XCourier), Retro, and Scalable (Bitstream Vera, DejaVu, Roboto). Sizes and styles are included with each pack. Installing a pack does not change the active Workbench, system or screen font.

    TrueType packs also install **ttf.library**. The first-boot wizard registers their fonts before regional settings. Failed registration is retried on the next boot without repeating the interactive setup. Downloads come from the original sources, not from CaffeineOS. MagicWB is shareware; its license requires registration after the evaluation period and does not permit separate redistribution of its fonts.

7. **Network tab.** Choose Roadshow, MiamiDX or AmiTCP_NG and enter DHCP, static or wifi settings. Roadshow archives and MiamiDX registration files can be supplied here. Connections start from the Workbench tools.

    [![Network tab with Roadshow and static ethernet](assets/screenshots/network.png)](assets/screenshots/network.png){ target="_blank" }

8. **Output tab.** Pick how to deliver the build:

    - **Image file** - writes a sparse **.img**.
    - **Image file + flash to SD card** - keeps the image and then flashes it; fastest for large builds.
    - **Direct to SD card** - skips the image, but is slower with large partitions.

    !!! danger "Double-check the target!"
        **Picking the wrong disk will wipe it.** Emu68 Hatcher will refuse to write to mounted root partitions (=your operating system) but has no problem with wiping anything else you have connected.

    [![Output tab set to build an image and flash it to SD](assets/screenshots/output.png)](assets/screenshots/output.png){ target="_blank" }

9. **Partitions tab.** Add or resize partitions and optional extra-content folders. Default is a 64 GB image with 1 GB **EMU68BOOT**, about 1/15 for Workbench and the rest unallocated. Add further Amiga partitions manually or select content in the AGS tab. FAT32 and RDB use two MBR entries; the listed Amiga partitions are inside the RDB.

    Extra content is copied last and can overwrite generated files.

    [![Partitions tab with four Amiga partitions](assets/screenshots/partitions.png)](assets/screenshots/partitions.png){ target="_blank" }

10. **AGS tab (optional).** Select **Import AGS** and choose a local source image. Hatcher checks it automatically. WHDLoad games, demos and AGS, extra games and Premium, and emulators and applications are selected by default; Media is optional. Content choices update the planned partitions immediately. Sizes, remaining space and the layout appear in the AGS tab. If the content does not fit, deselect content, choose a larger target or use **Adjust partitions…**. Existing manual partitions keep their sizes. See [AGS import](ags.md) for supported source profiles, partition rules and runtime limits.

11. **Click "Build Image".** The first build downloads packages; later builds use the cache. The dialog and **buildlog.txt** contain the build log.

Flashing asks for admin access. On macOS, hst-imager also needs Full Disk Access; see [Installation](installation.md#macos).

## Save / load configuration

Use **Save Config...** and **Load Config...** for JSON configs. wifi credentials are not saved.

## RGB2RTG

RGB2RTG v0.73 is an optional experimental installation for an A1200 with
PiStorm32-lite and a Raspberry Pi 4 or CM4. Pi 3 is not tested. Select it in the
Emu68 tab and browse to your local **RGB2RTG_A1200_v0.73.7z** release from
[the author](https://astair86.itch.io/rgb2rtg-amiga1200). Hatcher checks the release
files before building. The archive is not included or downloaded automatically.

Choose PAL (1080p50) or NTSC (1080p60). This sets and locks the HDMI mode on the
Display tab while RGB2RTG is enabled. Use a VideoCore Workbench screen mode,
and disable Framethrower/C790 capture. Read/write eMMC unit 0 access is required
for the command to save boot settings. Enabling RGB2RTG selects the matching
Emu68 1.1.0-beta.1 baseline and installs ToolsDaemon and Picasso96 dependencies.
The custom kernel and VideoCore 1.5 driver are installed together.

The desktop menu is **System → RGB2RTG**, with **On/off...**, **RTG Image...**
and **About...**. Saved preferences are applied during User-Startup. New
installations default to `picture=off`: the RTG desktop still shows, but native
Amiga screens show black on HDMI after the preferences are applied. Turn the
picture on through **On/off...** or run `C:rgb2rtg ON`; the choice is saved for
later boots. Existing preferences are kept. This does not disable the RGB2RTG
kernel or its boot-time capture setup.

Power cycle the Amiga after installation and after changing its video standard
or HDMI rate.

To return to the baseline kernel and driver, run this from an Amiga Shell:

```text
Execute SYS:Emu68-Hatcher/RGB2RTG/Recover
```

Recovery removes Hatcher's RGB2RTG submenu and startup block while retaining
other menu and startup edits. It restores both boot configurations, including
the FirstBoot backup if still present, before restoring the matched baseline
driver. If recovery reports a failure, complete the indicated restore before
power cycling. Copies of the baseline configuration, driver, menu and startup
files remain for manual recovery. Power cycle the Amiga to start the restored
kernel and driver and reload the menus.

Extra Files are copied after this installation. Replacing the kernel, driver,
monitor icon, boot configuration or startup files there can break RGB2RTG and
its recovery. Hardware acceptance still requires testing on the A1200; a host
build or emulator cannot verify the capture path.
