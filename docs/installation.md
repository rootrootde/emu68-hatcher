# Install

## Requirements

- **macOS 12+** (Apple Silicon or Intel) - the **.app** bundle includes Python, no separate install needed
- **Windows 10/11** (x64 or arm64) - the **.exe** installer includes Python, no separate install needed
- **Linux** (recent Ubuntu/Debian, Fedora, Arch, …) - **.deb** installer for Debian/Ubuntu (x64 / arm64), manual Python install everywhere else
- **a SD card**, 4 GB minimum, 32+ GB recommended
- **a Kickstart ROM** + **Workbench ADF floppy images** (3.1 / 3.2 / 3.2.2.1 / 3.2.3 - pick one), or the **AmigaOS 3.9 CD image** (**.iso**) with a Kickstart 3.1 ROM:

| Workbench | ROM used by automatic selection |
|---|---|
| 3.1 | A1200 40.068 (or Cloanto 45.064 / 45.066 from Amiga Forever) |
| 3.2 | A1200 47.96 |
| 3.2.2.1 | A1200 47.111 |
| 3.2.3 | A1200 47.115 |
| 3.9 (CD **.iso**) | A1200 40.068 (the BoingBag soft ROM update loads at boot) |

!!! note "Why is an A1200 ROM selected?"
    Automatic selection follows Emu68 Imager and prefers an A1200 ROM when available. It also works on an A600 with PiStorm/Emu68. Manual selection is not available yet.

## Pick your OS

=== "macOS"

    <a id="macos"></a>

    ### Native installer (recommended)

    Download the latest **.dmg** for your Mac (arm64 for Apple Silicon, x64 for Intel) from the [releases](https://github.com/rootrootde/emu68-hatcher/releases) page, open it and drag **Emu68 Hatcher.app** into /Applications.

    !!! warning "First run: grant Full Disk Access to hst-imager"
        macOS protects raw disk access, even on removable media like SD cards, so an app needs explicit permission from you to write to one. Emu68 Hatcher uses **hst-imager** to write the card, so it's hst-imager that needs Full Disk Access - not the app itself.

        Open the app, click **Download Missing Tools** under **Overview → Required tools**. After hst-imager downloads, a dialog asks to register it with macOS - click **Set Up Now**, enter password, then enable **hst-imager** in the settings pane that opens.

    ### Install from source

    Needs Python 3.10+. Get the source tarball from the [releases](https://github.com/rootrootde/emu68-hatcher/releases) page (or git clone the repository) then run **bootstrap.py**:

    ```bash
    # from a release tarball
    tar xf emu68hatcher-<version>.tar.gz && cd emu68-hatcher-<version>

    # or from git
    git clone https://github.com/rootrootde/emu68-hatcher.git && cd emu68-hatcher

    python3 bootstrap.py
    emu68hatcher
    ```

=== "Linux"

    ### Native installer (Debian / Ubuntu)

    Download the latest **.deb** for your machine (x64 or arm64) from the [releases](https://github.com/rootrootde/emu68-hatcher/releases) page and install:

    ```bash
    sudo apt install ./emu68hatcher-*-linux-*.deb
    ```

    Then run **emu68hatcher** to launch the GUI.

    !!! note "other Linux distros"
        No RPM or AUR packages available yet. On Fedora / openSUSE / Arch, use the manual install below.

    ### Install from source

    Needs Python 3.10+. Grab the source tarball from the [releases](https://github.com/rootrootde/emu68-hatcher/releases) page (or **git clone** the repo if you'd rather), then run **bootstrap.py**:

    ```bash
    # from a release tarball
    tar xf emu68hatcher-<version>.tar.gz && cd emu68-hatcher-<version>

    # or from git
    git clone https://github.com/rootrootde/emu68-hatcher.git && cd emu68-hatcher

    python3 bootstrap.py
    emu68hatcher
    ```


=== "Windows"


    ### Native installer (recommended)

    Download the latest **.exe** for your machine (x64 or arm64) from the [releases](https://github.com/rootrootde/emu68-hatcher/releases) page and run it. The installer puts the app + bundled Python in **C:\Program Files\Emu68 Hatcher\\** and adds a Start menu entry.

    !!! note "SmartScreen"
        On first run Windows SmartScreen may show a "Windows protected your PC" dialog. Click **More info** → **Run anyway**.

    ### Install from source

    Needs Python 3.10+ from [python.org](https://www.python.org/downloads/) (tick **Add Python to PATH** during install). Grab the source tarball from the [releases](https://github.com/rootrootde/emu68-hatcher/releases) page (or **git clone** the repo if you'd rather), then run **bootstrap.py**:

    ```powershell
    # from a release tarball (Windows 10+ has tar built in)
    tar xf emu68hatcher-<version>.tar.gz; cd emu68-hatcher-<version>

    # or from git
    git clone https://github.com/rootrootde/emu68-hatcher.git; cd emu68-hatcher

    python bootstrap.py
    emu68hatcher
    ```
