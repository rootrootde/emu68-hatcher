# AGS partition import

**Storage → AGS import** copies complete filesystem partitions from a local AGS image. Start AGS through **WHDLoad:AGS** on the Workbench installed by Hatcher. The source Workbench, boot scripts and FAT partition are not imported.

**WHDLoad:AGS** and **WHDLoad:AGS2/Start_AGS** retain their original contents. The required paths and assigns are set in **S:User-Startup** when Workbench boots.

## Selection and layout

| Selection | Source volume | Default |
| --- | --- | --- |
| WHDLoad & AGS | WHDLoad, including AGS2, games, demos and magazines | Required |
| Extra games and Premium | Games | On |
| Work | Work (complete source partition) | On |
| Media | Media | Off |

Select **Import AGS** to reveal the source and content controls, then choose a local **.img** or **.hdf**. Hatcher checks the source automatically and shows the size of each content group. Selecting or deselecting content updates the planned partitions immediately. **Refresh** repeats the source check. Supported profiles are v30 and v31 beta 160726. The source must contain a direct RDB with 16 heads, 63 sectors and 512-byte blocks. Other geometries and network-share sources are rejected before target initialization.

Each imported volume keeps its original name and directory structure. Device names are allocated from the target layout and can be changed. Imported sizes are the exact source partition lengths, including unused filesystem blocks. Size, volume, filesystem and boot status are locked. Copies use the Hatcher PFS3 handler, are automatically mounted and are not bootable. Changing PDS3 to PFS3 affects the RDB entry; the copied filesystem is not formatted or converted.

The default layout contains **EMU68BOOT** and **Workbench**, with the remaining RDB space unallocated. Add other partitions in **Storage → Partition layout** or select content in **AGS import**. Loading a configuration restores the layout and checks its AGS source again. A manual Work volume conflicts with an imported Work volume and must be renamed or removed explicitly. Existing partitions are never shrunk automatically to fit AGS. Imported partitions cannot receive extra-content folders. The partition editor marks them **AGS · fixed**. Change their selection in **AGS import** above the layout.

Configuration version 1.3.0 distinguishes partition copies from the former file import. Loading an older AGS configuration sets its allocation to pending. The former Emulators selection proposes the whole Work partition, including applications. Former reservations become ordinary partitions and remain in the layout. The source is checked automatically. Resolve any conflicts shown in **Storage → AGS import** before building.

Storage shows import controls and one layout bar on the same scrolling page. If the selection exceeds available space, AGS import shows the shortfall without discarding the selection. Deselect content or adjust capacity and manual partitions under **Partition layout**. Building remains blocked until the layout is valid. Disabling AGS removes its imported partitions from the configuration. These edits do not write to an image or card; the physical-disk confirmation still appears when starting a build.

## Portable launcher

Original game starters and menus remain on the copied partition. The AGS block in **S:User-Startup** supplies **AGS:**, **Scripts:**, **AGSOS:**, **WHD_Games:** and **WHD_Demos:**. Work supplies **Emulators:** and Games supplies **Premium:**. Media supplies **ST-00:**. No new script assumes fixed SDH device numbers.

AGS detects portable mode through the absence of **S:AGS-Stuff** and hides its Boot and Disk options itself. AGS does not detect the host itself. At every boot Hatcher sets **PiStorm** to 1 and **HW** to **Real** on a detected PiStorm, so AGS never runs its emulator commands on the Amiga. In an emulator it sets **PiStorm** to 0 and defaults **HW** to **Amiberry**; an emulator chosen in AGS's **Set HW** menu is kept. Nothing is written to **ENVARC:**, so a card tested in an emulator still boots as real hardware. Save-directory setup uses the original AGS behavior.

In Check_Drives, Hatcher replaces volume-only InfoNew checks for its portable assigns with Assign EXISTS. This lets AGS recognize content exposed through directory assigns, including Work:Emulators.

Missing Ex, kgiconload, WBLoad and WBRun helpers are supplied from AGS. Existing Workbench versions are retained. Additional AGS commands, libraries and fonts are available through the portable search paths. The import leaves **S:WHDLoad.prefs** and **S:WHDLoad-Startup** unchanged. AGS can subsequently write these files through its original Speed_Reset and Quit-Key functions.

When a VideoCore Workbench mode is selected, Hatcher sets **AGS2.conf** and every **Themes/*.conf** to VideoCore 640x256 with 256 colours. AGS keeps its own resolution regardless of the selected Workbench resolution. Backgrounds, screenshots and layout coordinates remain unchanged. Native Workbench builds retain the source image's menu and theme modes.

The **Themes_AGAtoRTG** and **Themes_RTGtoAGA** scripts switch between these VideoCore and native PAL modes. They also work when the optional **- Use RTG Screen -.run** and **- Use AGA Screen -.run** menu entries are absent. To switch from an Amiga Shell, run **Execute AGS:Scripts/Themes_AGAtoRTG** or **Execute AGS:Scripts/Themes_RTGtoAGA**, then restart AGS. WHDLoad games still use their own screen modes and video output.

Search, favourites, filters, random launch, documentation, themes and music remain available through their original scripts. Their presence does not establish runtime compatibility. Work applications are copied but not started automatically. **Emulators1:** and **Emulators2:** are not assigned without a verified source mapping; the v30 ScummVM readme still refers to Emulators2:. Missing emulator data, icons, slaves or commercial Premium data remain source issues.

Napalm temporarily changes **ENV:RtgMaster**. onEscapee invokes SetPatch and adds library and font assigns. These starters remain and need runtime checks.

## Copying and verification

Hatcher checks source identity before target initialization and around partition copies. Source and output must be different files, and the source cannot be stored on the selected output or flash disk. Capacity checks include the output image's full configured size, temporary staging and any cross-volume move.

Copies run in the applied partition order. Normal partitions are created and formatted; imported partitions are copied directly and never formatted afterward. The AGS stage adds startup assigns and missing helpers to the boot partition. It stages the corrected Check_Drives and theme-switch scripts on the imported WHDLoad partition, along with the theme configurations for RTG builds. Their hashes are verified after copying. Workbench directory metadata corrections remain independent of AGS; imported game trees need no host metadata conversion.

Production checks compare HST's reported copy byte count with the planned length, then inspect target geometry, partition order, volume names and filesystem flags. Original AGS profile markers and generated startup setup are read back and hashed before cleanup. A successful HST exit code alone is insufficient: the tested version can report success after copying zero bytes. Production checks do not compare every copied block or enumerate every game file.

The development copy test compared all 2,147,991,552 bytes of the v30 Work partition and verified a small write/readback in the copied filesystem. Hardware startup, games, emulators, saving and return to Workbench still require testing on an explicitly selected Amiga/PiStorm system. Flash verification is unchanged.
