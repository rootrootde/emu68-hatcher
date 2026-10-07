# <img src="assets/emu-icon.svg" width="96" height="96" alt="logo"> Emu68 Hatcher

Build ready-to-run SD cards with pre-configured Workbench installation (+batteries included) for [PiStorm](https://github.com/captain-amygdala/pistorm)-accelerated Amigas. 

Runs on macOS, Linux and Windows.

<a href="assets/screenshots/overview.png" target="_blank"><img src="assets/screenshots/overview.png" alt="Emu68 Hatcher configuration overview (offscreen Qt)" width="900"></a>

- Bootable Emu68 install for pistorm32-lite, pistorm and pistorm16
- Workbench install from stock ADFs (3.1 / 3.2 / 3.2.2.1 / 3.2.3), identified by hash
- AmigaOS 3.9 install from CD **.iso** and Kickstart 3.1, including BoingBag 1+2
- Customizable [package set](packages.md): MUI, WHDLoad+WHDLoadWrapper, IBrowse, HippoPlayer, ViNCEd, ...
- RTG via Picasso96, with optional full **Picasso96.lha**
- Roadshow, MiamiDX or AmiTCP_NG networking, with wifi and ethernet drivers
- Partition layout editor (PFS3 + FFS), optional per-partition "extra content" directory to pre-load Work / Data volumes (WHDLoad games, backups, ...)
- Build configs as JSON (save / load)
- Build to **.img** file (sparse by default), flash to SD card after build, or write straight to SD card
- Application and signed package-list update checks

## Known issues / limitations

- **mostly tested on 3.2(.3)** - expect more potential issues with 3.1
- Automatic ROM selection prefers A1200 ROMs; this also works on A600. No manual selection yet.

## Support + Feedback

For questions, feedback, bug reports, feature requests, and project updates:

[Join the Discord](https://discord.com/invite/ApTbasXJPE){ .md-button .md-button--primary }

!!! warning "Please don't ask for support on other Amiga Discord servers"
    I won't be able to keep up with multiple channels, and the Emu68 Hatcher server is a better place for discussions plus I can post updates there more frequently.

[Open a GitHub issue](https://github.com/rootrootde/emu68-hatcher/issues){ .md-button }

!!! tip "Reporting an error"
    Attaching the right log file makes debugging way easier. See [Troubleshooting](troubleshooting.md) for which file to grab depending on whether the build itself failed or something went wrong on the AmigaOS side.

## Credits

Thanks to:

- [mja65](https://github.com/mja65)'s fantastic work on the [Emu68 Imager](https://github.com/mja65/Emu68-Imager-Software) project
- [Emu68](https://github.com/michalsc/Emu68) and [Emu68-tools](https://github.com/michalsc/Emu68-tools) by Michal Schulz (MPL-2.0) - m68k emulation and the on-Amiga companion tools (EmuControl, VideoCore.card, WiFiPi.device, ...)
- [hst-imager](https://github.com/henrikstengaard/hst-imager) and [hst-amiga](https://github.com/henrikstengaard/hst-amiga) by Henrik Stengaard (MIT) - disk image + RDB tooling
- [Emu68P96Settings](https://github.com/flype44/Emu68P96Settings) by flype44 - format reference for reading and generating Picasso96 settings

Bundled / downloaded at build time:

- [WHDLoad](http://whdload.de/)
- [Roadshow Demo](https://www.amigashop.org/product_info.php?cPath=2_34&products_id=200&language=de) bundled with permission from A. Magerl (APC&TCP)
- [MiamiDX 1.0c](https://aminet.net/package/comm/tcp/MiamiDx10cmain) downloaded from Aminet when selected
- [7-Zip](https://github.com/ip7z/7zip) (GNU LGPL) - downloaded at install time, License.txt copied alongside the binary
- [Poseidon USB stack](https://wiki.icomp.de/wiki/RapidRoad) by Chris Hodges - downloaded from the iComp wiki when USB support is selected
- Aminet packages (MUI, HippoPlayer, IBrowse, akDatatypes, Picasso96, ...) - downloaded from [aminet.net](https://aminet.net) at build time; each ships its own readme with license
