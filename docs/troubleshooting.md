# Troubleshooting

## Emu68 Hatcher (host)

Build failures write **buildlog.txt** next to the **.img** file. Direct-to-SD builds put it in the app cache; the path is shown at the top of the log. Please attach it to bug reports.

### macOS: build fails with "Operation not permitted" on /dev/disk\*

hst-imager needs Full Disk Access. See [Installation → macOS](installation.md#macos).

### Corrupted downloads or tools

**Reset App Data** deletes downloaded tools, packages and temporary files. Saved configs stay.

### Update or package-list check failed

The app keeps the last verified package list, or its bundled copy. Check the warning tooltip and
try **Check for Updates** again. This can also fix package 404 or checksum errors.

If an app update will not open, use **Open Downloads Folder**. Linux installation is only offered
for Debian-based systems.

### Required ADF or icon set is missing

Disks are matched by content, not filename. **Show details...** lists missing media; GlowIcons
needs its matching ADF.

## AmigaOS / Workbench

The Amiga-side tools run in CON windows: **SYS:Utilities/Network Config** keeps its window open after it exits so the output can be read or copied, while the Connect WiFi / Connect Ethernet launchers close their window automatically when they finish.

### Browser reports a DNS error

Open **SYS:Utilities/Network Config** and check the interface, gateway and DNS servers. Save and
reconnect. Static address, netmask and gateway have to match.

Issue tracker: <https://github.com/rootrootde/emu68-hatcher/issues>
