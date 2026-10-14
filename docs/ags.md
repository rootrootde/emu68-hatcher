# AGS import

**Storage → AGS import** copies the game partitions of a local AGS image onto the new card. The source Workbench, its boot scripts and the FAT partition are not imported; Hatcher's own Workbench starts AGS instead.

## Import

Tick **Import AGS** and choose the AGS **.img** or **.hdf**. Hatcher checks the source right away and shows the size of each content group; **Refresh** repeats the check. Supported are the v30 and v31 beta (160726) images. The source has to be a local file with a direct RDB (16 heads, 63 sectors, 512-byte blocks), and it cannot be the output image or sit on the SD card being written.

| Selection | Source volume | Default |
| --- | --- | --- |
| WHDLoad & AGS | WHDLoad, including AGS2, games, demos and magazines | Required |
| Extra games and Premium | Games | On |
| Work | Work (complete partition) | On |
| Media | Media | Off |

Each selected volume becomes a partition in **Partition layout** with its original name, the exact source size and no boot flag. Those rows are marked **AGS · fixed**: size, volume, filesystem and boot status are locked, and they take no extra files. Change what is imported in **AGS import**, not in the table. If the selection does not fit, the shortfall is shown; deselect content or raise the capacity. Disabling the import removes the partitions again.

Configurations saved before 1.3.0 are loaded with the import pending; check **AGS import** for conflicts before building. A manual **Work** volume clashes with an imported one and has to be renamed or removed.

## On the Amiga

Start AGS through **WHDLoad:AGS**, or from the **Games** menu when ToolsDaemon is installed; the menu also starts iGame. **S:User-Startup** sets the assigns AGS needs (**AGS:**, **Scripts:**, **AGSOS:**, **WHD_Games:**, **WHD_Demos:**, plus **Emulators:**, **Premium:** and **ST-00:** for the optional volumes), so no script depends on fixed device numbers.

AGS runs in its portable mode and hides its boot and disk options. On a PiStorm it never runs the emulator commands; in an emulator it defaults the hardware setting to Amiberry. Nothing is written to **ENVARC:**, so a card tested in an emulator still boots as real hardware.

Games start through the WHDLoad in **C:**, with or without the WHDLoad wrapper; the copies AGS brings along are removed. **S:WHDLoad.prefs** and **S:WHDLoad-Startup** stay as they are.

With a VideoCore Workbench mode, AGS and its themes are set to VideoCore 640x256 in 256 colours. **Execute AGS:Scripts/Themes_RTGtoAGA** switches back to the native PAL modes, **Themes_AGAtoRTG** the other way; restart AGS afterwards. WHDLoad games keep their own screen modes.

Work applications are copied but not started automatically. **Emulators1:** and **Emulators2:** are not assigned. Missing emulator data, icons, slaves or Premium content are source issues, not import errors.
