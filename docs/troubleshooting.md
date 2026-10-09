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

**SYS:Utilities/Network Config** and **Network → Network Config** open Hatcher Prefs
(**C:Hatcher-Prefs**). Connect and disconnect are done in its window and ask for a
typed confirmation; there are no one-click connect icons any more.

The old ARexx tools stay on the card for this release as a fallback. From a Shell:

```
rx S:NetworkConfig.rexx                    ; Roadshow / AmiTCP_NG settings
rx S:NetworkConfig.rexx ONLINE WIFI        ; or ONLINE ETHERNET
rx S:MiamiNetwork.rexx CONFIG              ; MiamiDX settings
rx S:MiamiNetwork.rexx ONLINE GENET        ; or ONLINE WIFIPI, OFFLINE
```

### Clock is not set after boot (full Roadshow)

Boot-time time sync now runs **C:Hatcher-Prefs startup-time**, which does nothing
until you opt in once from a Shell with `Hatcher-Prefs migrate enable-startup-time SYS:`
and then retire that change as described in
**SYS:Emu68-Hatcher/Tools/Hatcher-Prefs/ReadMe.txt**. The opt-in is tied to the
installed program, so repeat it after updating Hatcher Prefs.
`Hatcher-Prefs startup-status SYS:` shows whether it is active.

### Browser reports a DNS error

Open **SYS:Utilities/Network Config** and check the interface, gateway and DNS servers. Save and
reconnect. Static address, netmask and gateway have to match.

Issue tracker: <https://github.com/rootrootde/emu68-hatcher/issues>
