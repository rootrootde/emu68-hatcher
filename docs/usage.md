# Usage

## Choose a work area

The sidebar has six areas. Visit them in any order; incomplete settings do not
prevent navigation. **Load Configuration…** and **Save Configuration…** are in
the File menu. Settings are not saved automatically, and WiFi credentials are
not written to configuration files.

### Overview

**Updates** is visible at the top when the app opens. The configuration summary
and compact build checks follow below. Click a check or activate it with the
keyboard to open its setting. **Required tools** contains tool downloads; the
**Tools** menu jumps there directly. Application downloads go to Downloads;
opening a Linux installer requires a Debian-based system.

[![Configuration overview](assets/screenshots/overview.png)](assets/screenshots/overview.png){ target="_blank" }

### System

- **Hardware & Emu68:** select the release and hardware settings. Boot, storage,
  CPU, compatibility and manual overrides are under **Show advanced settings**.
- **AmigaOS & files:** choose the Workbench version, add directories containing
  ROMs and installation media, and select languages. Files are matched by hash;
  **Show details…** lists the detected files. AmigaOS 3.9 uses the CD **.iso**
  and a Kickstart 3.1 ROM. Do not mount the ISO.
- **Network:** choose Roadshow, MiamiDX, AmiTCP_NG or None. Roadshow archives and
  MiamiDX registration-key folders belong here. DHCP/static addresses, routing,
  DNS and WiFi settings remain available where the selected stack supports them.

[![System hardware and release settings](assets/screenshots/system.png)](assets/screenshots/system.png){ target="_blank" }

### Appearance

HDMI output, Workbench screen mode, icons, Picasso96 and native-video capture
share one page, without subpages. **Force HDMI output without EDID** is directly
below the output mode. Selecting native-video capture reveals its device and
scaling controls. C790 requires Emu68 1.1 or later and a VideoCore Workbench mode.
Use **Browse…** to supply your full **Picasso96.lha**.

Available icons follow the selected OS version and detected media. GlowIcons
requires its matching ADF. Theme support remains disabled in this checkout;
there is no separate, mostly empty theme page.

[![Display settings](assets/screenshots/appearance.png)](assets/screenshots/appearance.png){ target="_blank" }

### Software

Select applications, bundles and hardware drivers. The list is filtered for the
chosen Workbench and Emu68 versions. The expandable **Required packages** group
shows automatic selections and their reasons. Network and theme choices are
edited in System and Appearance, not duplicated here. MUI remains selectable
when an application requires it. See [Packages](packages.md) for the catalog.

**Minimal** clears optional software, networking and theme selection. The OS,
RTG, filesystem and FirstBoot helpers remain, including fat95. AGS, extra files,
ROMs, display mode, partitions, icons and installation media are unchanged.
Extra files are still copied and can add software to a minimal image.

**Select None** clears software requests only; network and theme dependencies
can still be required. **Defaults** restores software defaults without changing
those owners. Saved requests remain separate from automatic dependencies.

Ethernet, WiFi, USB, NVMe, I2C clock support and diagnostic tools can be selected
separately. A network stack requires both Ethernet and WiFi support. USB and
NVMe are offered only for compatible Emu68 versions.

The **Fonts** group has four optional packs: Workbench (Apparent, Dina,
Terminus), MagicWB (XEN, XHelvetica, XCourier), Retro, and Scalable (Bitstream
Vera, DejaVu, Roboto). Installing a pack does not change the active font.
TrueType packs include **ttf.library**; first boot registers their fonts before
regional settings. Failed registration is retried without repeating the
interactive setup. Fonts come from their original sources, not CaffeineOS.
MagicWB is shareware and requires registration after its evaluation period.

[![Software selection](assets/screenshots/software.png)](assets/screenshots/software.png){ target="_blank" }

### Storage

Storage is one scrolling page: **Output target**, **AGS import**,
**Partition layout**, and **Extra files**. There are no nested tabs.
Capacity labels use GiB; card presets retain the existing
95% allowance for nominal card sizes. A selected card supplies its exact byte
capacity, locks the size control and leaves manual partitions/content choices
intact. A shortfall is shown instead of shrinking partitions.

Choose an output mode:

- **Image:** write an **.img** file, with optional sparse allocation.
- **Image + SD card:** retain the image and then write it to the selected card.
- **Direct to SD card:** skip the image file; large builds can be slower.

For image + card, **Verify after writing** starts a separate readback pass after
writing. The card stays unmounted between passes. All-zero image blocks are
skipped, so verification checks written blocks, not untouched sectors. Progress
and speed are shown separately. Verification requires the Hatcher fork of
hst-imager with `--verify-after`; older tools still support write-only flashing.

!!! danger "Double-check the target!"
    **Picking the wrong disk will wipe it.** Root-disk protection does not make
    another connected disk safe to erase. Check the selected device before
    confirming a build.

- **AGS import:** select **Import AGS** to reveal the source and content choices.
  Choose a local source image. Inspection
  starts automatically; **Refresh** checks the source again. WHDLoad/AGS is
  required. Games, the complete Work partition and Media are separate choices.
  Source changes discard old inspection results. Fixed imported sizes and
  capacity errors appear beside the choices. See [AGS import](ags.md).
- **Partition layout:** edit capacity, device/volume names, boot flags, filesystems
  and sizes, or add/remove manual partitions. Imported partitions show their
  origin and fixed-size restrictions; change their selection in **AGS import** above.
- **Extra files:** choose a destination partition and a source folder. The
  estimated size and usable filesystem capacity are shown together. Imported
  AGS partitions do not accept extra files. Extra files are copied last and can
  overwrite generated files.

The default is a nominal 64 GB image with **EMU68BOOT**, Workbench and the rest
unallocated. Unallocated capacity is not free space inside a filesystem.

[![Shared storage target and partition plan](assets/screenshots/storage.png)](assets/screenshots/storage.png){ target="_blank" }

### Review & build

The footer's **Review & build** button only opens this area. Review lists the
current checks and configuration summary. **Preview boot files…** opens the
combined Emu68, display and software result without exposing WiFi passwords.

The build button is available when blocking checks finish successfully. Its
label follows the output mode. Checks run again before starting; SD-card modes
warn about erasing the target. The existing progress dialog supports
cancellation and records the build log in **buildlog.txt**. The first build
downloads packages; later builds reuse the cache.

[![Review checks and build controls](assets/screenshots/review.png)](assets/screenshots/review.png){ target="_blank" }

Device writes request admin access during the build, not during review. On
macOS, hst-imager also needs Full Disk Access; see
[Installation](installation.md#macos).

Screenshots show the offscreen Qt interface without private installation media.
They illustrate navigation, not a completed image or device-write test.
