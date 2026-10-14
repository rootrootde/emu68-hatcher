# Troubleshooting

## Emu68 Hatcher (host)

Build failures write **buildlog.txt** next to the **.img** file. Direct-to-SD builds put it in the app cache; the path is shown at the top of the log. Please attach it to bug reports.

### macOS: build fails with "Operation not permitted" on /dev/disk\*

hst-imager needs Full Disk Access. See [Installation → macOS](installation.md#macos).

### Corrupted downloads or tools

**Reset App Data…** deletes downloaded tools, packages and temporary files. Saved configs stay.

### Update or package-list check failed

The app keeps the last verified package list, or its bundled copy. Check the warning tooltip and
try **Check now** again. This can also fix package 404 or checksum errors.

If an app update will not open, use **Open Downloads Folder**. Linux installation is only offered
for Debian-based systems.

### Required ADF or icon set is missing

Disks are matched by content, not filename. **Show details...** lists missing media; GlowIcons
needs its matching ADF.

## AmigaOS / Workbench

**Network → Network Settings** and the **Network Settings** icon in **SYS:Utilities**
open the Hatcher Prefs window (**C:Hatcher-Prefs**). Connect, disconnect and the
settings are all in that window. The old ARexx scripts stay in **S:** for the Shell:

```
rx S:NetworkConfig.rexx                    ; Roadshow / AmiTCP_NG settings
rx S:NetworkConfig.rexx ONLINE WIFI        ; or ONLINE ETHERNET
rx S:MiamiNetwork.rexx CONFIG              ; MiamiDX settings
rx S:MiamiNetwork.rexx ONLINE GENET        ; or ONLINE WIFIPI, OFFLINE
```

### Clock is not set after boot (full Roadshow)

With the full Roadshow archive, User-Startup syncs the clock after the network comes
up with `rx S:NetworkConfig.rexx SYNCTIME`, as in earlier releases. Check that
**Connect at boot** is on in Network Settings (it removes **S:Network-disabled**) and
that the network connects at boot.

### Emu68 Manager says the card has a newer version than is running

A reset does not reload the Pi firmware and kernel. Switch the Amiga off and on
again; the window then shows the same version for the card and the running system.

### Browser reports a DNS error

Open **Network Settings** and check the address, gateway and DNS servers. Save and
connect again. Static address, netmask and gateway have to match.

Issue tracker: <https://github.com/rootrootde/emu68-hatcher/issues>
