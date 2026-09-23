# AGS import

The AGS tab can import the v3.0 WHDLoad partition from a local AGS Classic AGA image. It copies the AGS2 menu and launcher together with its WHDLoad content to the selected partition under **AGS/WHDLoad/**. It does not replace the Workbench installed by the build.

## Set up the import

1. Select **Import AGS v3.0 WHDLoad games and launcher**.
2. Choose a local **.img** or **.hdf** source image. UNC paths are not supported.
3. Click **Inspect Source**. The source check runs in the background and reports the recognized profile, file count, content size and any warnings. The build checks the source again before importing it.
4. Select an Amiga content partition from the list. The conservative estimate adds 8 KiB per inventory entry, a 10% margin and a 512 MiB reserve for filesystem and metadata overhead. The build checks the destination again before finalizing, and checks host space for staging and output before it starts.
5. Save the build configuration if you want to reuse the source and partition choice later. **Save Config...** and **Load Config...** include these AGS settings.

Use an image file that is separate from the build output. The source cannot be on a disk selected for direct output or flashing. Leave enough free host storage for the staged import and the output image; a sparse image can grow as the build writes data.

## Start AGS

Build and boot to the Hatcher Workbench as usual. Open the **Start_AGS** icon at:

```text
SYS:Emu68-Hatcher/AGS/Start_AGS
```

The first release targets native AGA on an A1200 with PiStorm. It does not replace the system Startup-Sequence or boot straight into AGS. Amiga boot, launcher behavior, game starts, save handling and return to Workbench have not been tested on hardware.

## Scope

This release supports the v3.0 WHDLoad partition and its AGS launcher. The separate Games partition, Premium content, emulators and v3.1 images are not supported. Menu entries that depend on those sources are excluded. Some Premium titles also require commercial data that is not included in the source image.

The visible menus contain WHDLoad games, demos and disk magazines. AGS options, search, favourites and random starters are hidden; menu music and expert mode are disabled. These functions can reference excluded content or change the running system.

Choosing **Minimal** in the Software tab does not clear a selected AGS import. WHDLoad remains a required package for that import.

Partition extra content is copied after AGS and can overwrite its files. The build checks the required AGS structure before finalizing the image, so a missing or invalid launcher or required file causes the build to fail.
