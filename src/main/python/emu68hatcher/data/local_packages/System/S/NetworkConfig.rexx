/* NetworkConfig.rexx - Emu68 Hatcher network config tool
 *
 * Replaces NetworkManager.rexx and WifiConfig.rexx. Loops on a top-level
 * menu: WiFi/Ethernet/Offline actions plus a Settings submenu for WiFi
 * credentials, per-interface static IP / DHCP, DNS, and the Roadshow boot toggle.
 *
 * Invoked by 'rx s:NetworkConfig.rexx' - from the Network menu or from
 * SYS:Utilities/Network Config's double-click launcher.
 */

if ~show('l', 'rexxsupport.library') then
    call addlib('rexxsupport.library', 0, -30, 0)
if ~show('l', 'rexxreqtools.library') then do
    if ~addlib('rexxreqtools.library', 0, -30, 0) then do
        say "rexxreqtools.library is missing - cannot show requesters."
        exit 20
    end
end

/* paths and constants */
hWifipiCfg     = "DEVS:NetInterfaces/wifipi"
hGenetCfg      = "DEVS:NetInterfaces/genet"
hDnsCfg        = "DEVS:Internet/name_resolution"
hRoutesCfg     = "DEVS:Internet/routes"
hWirelessPrefs = "SYS:Prefs/Env-Archive/Sys/wireless.prefs"
hTuningsFile   = "S:RoadshowSettings"
hBootDisable   = "S:Network-disabled"
hAmiTcpMarker  = "LIBS:AmiTCP_NG.version"
hChoiceFile    = "T:hatcher-choice"
hWmLog         = "RAM:hatcher-wm.log"
hWifipiDevice  = "SYS:Devs/Networks/wifipi.device"
hGenetDevice   = "SYS:Devs/Networks/genet.device"
hAmiTcp        = exists(hAmiTcpMarker)

address command

/* non-interactive entry: 'rx NetworkConfig.rexx ONLINE WIFI|ETHERNET'.
   used by the Connect WiFi / Connect Ethernet launcher icons - bring up
   the named interface and exit so the launcher window auto-closes.
   missing wifi credentials offer the credentials requester first; on
   failure a requester carries the reason before exiting. */
parse arg hCmd hIface .
if upper(strip(hCmd)) = "ONLINE" then do
    hIface = upper(strip(hIface))
    hConnResult = 0
    select
        when hIface = "WIFI" then call HatcherOnlineWifi
        when hIface = "ETHERNET" then call HatcherOnlineGenet
        when hIface = "GENET" then call HatcherOnlineGenet
        otherwise say "Unknown interface: " || hIface
    end
    if hConnResult = 0 then do
        call rtezrequest("Could not connect via " || hIface || ".", "OK", "Hatcher Network")
        exit 10
    end
    /* -1 = user declined or aborted credential entry - already informed */
    if hConnResult ~= 1 then exit 0
    "C:Wait 2"
    exit 0
end

/* boot entry, run from User-Startup after Network-Startup. DHCP may still
   be settling, so retry for up to a minute before giving up quietly. */
if upper(strip(hCmd)) = "SYNCTIME" then do
    if ~exists("C:sntp") then exit 0
    do hTry = 1 to 6
        "C:sntp pool.ntp.org >NIL:"
        if rc = 0 then do
            /* server reachable - the shared routine does the sync + RTC save */
            call HatcherSyncTime
            exit 0
        end
        "C:Wait 10"
    end
    exit 5
end

/* main loop */
do forever
    "RequestChoice >"hChoiceFile ,
        "TITLE ""Hatcher Network""" ,
        "BODY ""Choose an action:""" ,
        "GADGETS ""WiFi|Ethernet|Offline|Settings|Exit"""
    hChoice = ReadChoice()
    select
        when hChoice = "1" then call HatcherOnlineWifi
        when hChoice = "2" then call HatcherOnlineGenet
        when hChoice = "3" then call HatcherOffline
        when hChoice = "4" then call SettingsMenu
        otherwise leave
    end
end
exit 0

SettingsMenu:
    do forever
        if hAmiTcp then
            "RequestChoice >"hChoiceFile ,
                "TITLE ""Hatcher Network: Settings""" ,
                "BODY ""Choose what to configure:""" ,
                "GADGETS ""WiFi creds|WiFi IP|Ether IP|DNS|Back"""
        else
            "RequestChoice >"hChoiceFile ,
                "TITLE ""Hatcher Network: Settings""" ,
                "BODY ""Choose what to configure:""" ,
                "GADGETS ""WiFi creds|WiFi IP|Ether IP|DNS|Boot|Back"""
        hSel = ReadChoice()
        select
            when hSel = "1" then call WifiCredsScreen
            when hSel = "2" then call InterfaceIpScreen "wifipi", hWifipiCfg
            when hSel = "3" then call InterfaceIpScreen "genet", hGenetCfg
            when hSel = "4" then call DnsScreen
            when hSel = "5" & ~hAmiTcp then call BootToggleScreen
            otherwise return
        end
    end
    return

ReadChoice:
    hC = ""
    if open("c", hChoiceFile, "R") then do
        if ~eof("c") then hC = strip(readln("c"))
        call close("c")
    end
    "Delete" hChoiceFile "QUIET >NIL:"
    return hC

/* -----------------------------------------------------------------------
 * Online / offline actions
 * --------------------------------------------------------------------- */

HatcherOnlineWifi:
    say ""
    say "--- Online: WiFi ---"
    say ""
    hConnResult = 0

    if ~exists("Libs:bsdsocket.library") then do
        say "No bsdsocket.library is installed."
        "C:Wait 4"
        return
    end
    if ~exists(hWifipiDevice) then do
        say "WiFi driver missing: " || hWifipiDevice
        "C:Wait 4"
        return
    end

    call ReadSsid
    if hSsid = "" then do
        "RequestChoice >"hChoiceFile ,
            "TITLE ""Hatcher Network""" ,
            "BODY ""No WiFi config.*nOpen WiFi credentials?""" ,
            "GADGETS ""Yes|No"""
        if ReadChoice() ~= "1" then do
            hConnResult = -1
            return
        end
        call WifiCredsScreen
        call ReadSsid
        if hSsid = "" then do
            say "WiFi configuration incomplete."
            "C:Wait 2"
            hConnResult = -1
            return
        end
    end

    say "Resetting network stack..."
    "C:Netshutdown >NIL:"
    call HatcherKillWm

    say "Starting WirelessManager..."
    "Run >NIL: C:WirelessManager DEVICE="hWifipiDevice ,
        "CONFIG="hWirelessPrefs "VERBOSE >"hWmLog

    say "Waiting for WiFi link..."
    "C:WaitUntilConnected DEVICE="hWifipiDevice "Unit=0 DELAY=100"
    if rc ~= 0 then do
        say "Could not associate with the configured WiFi network."
        call HatcherKillWm
        return
    end
    say "WiFi link established."

    call HatcherApplyTunings "WIFIPI"
    call HatcherAttach "wifipi"
    call HatcherSyncTime

    say ""
    say "Connected via WiFi."
    "ShowNetStatus"
    return

HatcherOnlineGenet:
    say ""
    say "--- Online: Ethernet ---"
    say ""
    hConnResult = 0

    if ~exists("Libs:bsdsocket.library") then do
        say "No bsdsocket.library is installed."
        "C:Wait 4"
        return
    end
    if ~exists(hGenetDevice) then do
        say hGenetDevice || " is missing - Ethernet driver not installed."
        "C:Wait 4"
        return
    end

    say "Resetting network stack..."
    "C:Netshutdown >NIL:"
    call HatcherKillWm

    call HatcherApplyTunings "GENET"
    call HatcherAttach "genet"
    call HatcherSyncTime

    say ""
    say "Connected via Ethernet."
    "ShowNetStatus"
    return

HatcherOffline:
    say ""
    say "--- Offline ---"
    say ""
    call HatcherKillWm
    say "Shutting down TCP/IP stack..."
    "C:Netshutdown >NIL:"
    say "Disconnected."
    return

HatcherAttach:
    parse arg hDev
    say "Adding " || hDev || " network interface..."
    "AddNetInterface " || hDev || " TIMEOUT=50"
    if rc ~= 0 then do
        say "Error: could not add network interface (rc=" || rc || ")."
        if hDev = "wifipi" then call HatcherKillWm
        hConnResult = 0
    end
    else
        hConnResult = 1
    return

HatcherKillWm:
    "Status COM=C:WirelessManager >"hChoiceFile
    hWmPid = ""
    if open("c", hChoiceFile, "R") then do
        if ~eof("c") then hWmPid = strip(readln("c"))
        call close("c")
    end
    "Delete" hChoiceFile "QUIET >NIL:"
    if hWmPid ~= "" & datatype(hWmPid, "W") then do
        say "Stopping WirelessManager (pid " || hWmPid || ")..."
        "Break "hWmPid
        "C:Wait 2"
    end
    return

HatcherApplyTunings:
    parse arg hTarget
    hTarget = upper(hTarget)
    if hAmiTcp then return
    if ~exists("C:roadshowcontrol") then return
    if ~open("t", hTuningsFile, "R") then return
    do while ~eof("t")
        hRow = strip(readln("t"))
        if hRow = "" then iterate
        if left(hRow, 1) = ";" then iterate
        parse var hRow hDev ";" hKey ";" hVal
        if upper(strip(hDev)) ~= hTarget then iterate
        hKey = upper(strip(hKey))
        hVal = strip(hVal)
        select
            when hKey = "TCPRECEIVE" then ,
                "roadshowcontrol tcp.recvspace="hVal" >NIL:"
            when hKey = "UDPRECEIVE" then ,
                "roadshowcontrol udp.recvspace="hVal" >NIL:"
            when hKey = "TCPSEND" then ,
                "roadshowcontrol tcp.sendspace="hVal" >NIL:"
            when hKey = "UDPSEND" then ,
                "roadshowcontrol udp.sendspace="hVal" >NIL:"
            otherwise nop
        end
    end
    call close("t")
    return

HatcherSyncTime:
    if ~exists("C:sntp") then return
    say "Syncing system time..."
    "C:sntp pool.ntp.org >NIL:"
    if rc ~= 0 then do
        say "  (time sync skipped: could not reach pool.ntp.org)"
        return
    end
    if exists("C:SetDST") then "C:SetDST NOASK NOREQ QUIET >NIL:"
    "C:sntp pool.ntp.org >NIL:"
    call HatcherSaveClock
    return

/* only PiStorm can use the Pi I2C registers, even if the tool is installed. */
HatcherSaveClock:
    hUseI2C = 0
    if exists("C:SetClockI2C") then do
        "C:Version brcm-emmc.device >NIL:"
        if rc < 20 then hUseI2C = 1
        else do
            "C:Version brcm-sdhc.device >NIL:"
            if rc < 20 then hUseI2C = 1
        end
    end
    if hUseI2C then do
        "C:SetClockI2C SAVE >NIL:"
        if rc < 5 then return
    end
    "C:SetClock SAVE >NIL:"
    return

ReadSsid:
    hSsid = ""
    if ~exists(hWirelessPrefs) then return
    if ~open("p", hWirelessPrefs, "R") then return
    do while ~eof("p")
        hLine = upper(strip(readln("p")))
        if pos("SSID=", hLine) > 0 then do
            parse var hLine . 'SSID="'hSsid'"'
            leave
        end
    end
    call close("p")
    return

/* -----------------------------------------------------------------------
 * Settings screens
 * --------------------------------------------------------------------- */

WifiCredsScreen:
    hOldSsid = ""
    if exists(hWirelessPrefs) then do
        if open("p", hWirelessPrefs, "R") then do
            do while ~eof("p")
                hLine = strip(readln("p"))
                if pos("SSID=", upper(hLine)) > 0 then do
                    parse var hLine . 'ssid="'hOldSsid'"'
                    leave
                end
            end
            call close("p")
        end
    end

    hSsid = rtgetstring(hOldSsid, "Enter WiFi SSID:", "WiFi credentials")
    if rtresult = 0 then return
    hSsid = strip(hSsid)
    if hSsid = "" then return

    hPwdPrompt = "Enter WiFi password" || '0a'x || "(empty = open network):"
    hPsk = rtgetstring("", hPwdPrompt, "WiFi credentials")
    if rtresult = 0 then return
    hPsk = strip(hPsk)

    if ~open("w", hWirelessPrefs, "W") then do
        hErrMsg = "Could not write:" || '0a'x || hWirelessPrefs
        call rtezrequest(hErrMsg, "OK", "WiFi credentials")
        return
    end
    call writeln("w", "network={")
    call writeln("w", '   ssid="' || hSsid || '"')
    if hPsk = "" then
        call writeln("w", "   key_mgmt=NONE")
    else
        call writeln("w", '   psk="' || hPsk || '"')
    call writeln("w", "   scan_ssid=1")
    call writeln("w", "}")
    call close("w")

    hSavedMsg = "WiFi credentials saved." || '0a'x || '0a'x || "SSID: " || hSsid
    call rtezrequest(hSavedMsg, "OK", "WiFi credentials")
    return

InterfaceIpScreen:
    parse arg hLabel, hCfgPath

    /* read existing values for prefill */
    hCurAddr = ""
    hCurMask = ""
    if hAmiTcp then
        hCurGw = ""
    else
        hCurGw = ReadDefaultRoute(hRoutesCfg)
    if exists(hCfgPath) then do
        if open("f", hCfgPath, "R") then do
            do while ~eof("f")
                hLine = strip(readln("f"))
                if hLine = "" then iterate
                if left(hLine, 1) = "#" then iterate
                parse var hLine hK "=" hV
                hK = strip(hK)
                hV = strip(hV)
                select
                    when hK = "address" then hCurAddr = hV
                    when hK = "netmask" then hCurMask = hV
                    when hK = "gateway" & hCurGw = "" then hCurGw = hV
                    otherwise nop
                end
            end
            call close("f")
        end
    end

    "RequestChoice >"hChoiceFile ,
        "TITLE ""Hatcher: " || hLabel || " IP""" ,
        "BODY ""Use DHCP or static configuration for " || hLabel || "?""" ,
        "GADGETS ""DHCP|Static|Cancel"""
    hMode = ReadChoice()
    if hMode = "0" then return

    hNewMode = "dhcp"
    hNewAddr = ""
    hNewMask = ""
    hNewGw   = ""
    hTitle = hLabel || " IP"
    if hMode = "2" then do
        hNewMode = "static"
        hNewAddr = rtgetstring(hCurAddr, "IP address (e.g. 192.168.1.42):", ,
            hTitle)
        if rtresult = 0 then return
        hNewAddr = strip(hNewAddr)
        if hNewAddr = "" then return

        hMaskDefault = hCurMask
        if hMaskDefault = "" then hMaskDefault = "255.255.255.0"
        hNewMask = rtgetstring(hMaskDefault, "Subnet mask:", hTitle)
        if rtresult = 0 then return
        hNewMask = strip(hNewMask)
        if hNewMask = "" then return

        hNewGw = rtgetstring(hCurGw, "Gateway (empty = none):", hTitle)
        if rtresult = 0 then return
        hNewGw = strip(hNewGw)
    end

    hRc = WriteInterfaceCfg(hCfgPath, hNewMode, hNewAddr, hNewMask, ,
        hNewGw, hAmiTcp, hDnsCfg)
    if hRc = 0 then do
        call rtezrequest("Could not write " || hCfgPath, "OK", hTitle)
        return
    end

    if hNewMode = "static" then do
        if ~hAmiTcp then do
            if ~WriteDefaultRoute(hRoutesCfg, hNewGw) then do
                call rtezrequest("Could not write " || hRoutesCfg, "OK", hTitle)
                return
            end
        end
        hMsg = hLabel || " set to static" || '0a'x || ,
            "address=" || hNewAddr || '0a'x || ,
            "netmask=" || hNewMask
        if hNewGw ~= "" then
            hMsg = hMsg || '0a'x || "default route=" || hNewGw
    end
    else do
        if ~hAmiTcp then do
            if ~WriteDefaultRoute(hRoutesCfg, "") then do
                call rtezrequest("Could not write " || hRoutesCfg, "OK", hTitle)
                return
            end
        end
        hMsg = hLabel || " set to DHCP"
    end
    call rtezrequest(hMsg, "OK", hLabel || " IP")
    return

WriteInterfaceCfg: procedure
    parse arg hCfgPath, hMode, hAddr, hMask, hGateway, hUseAmiTcp, hDnsPath

    /* drop tool-managed keys; keep everything else as-is. gateway= is
       also dropped to clean up files written by older versions - roadshow
       rejects it as an unknown keyword. */
    hLines.0 = 0
    if exists(hCfgPath) then do
        if open("r", hCfgPath, "R") then do
            do while ~eof("r")
                hOne = readln("r")
                hCheck = strip(hOne)
                hKey = ""
                if hCheck ~= "" & left(hCheck, 1) ~= "#" then do
                    parse var hCheck hKey "=" .
                    hKey = strip(hKey)
                end
                hKeep = 1
                if hKey = "configure" then hKeep = 0
                if hKey = "address"   then hKeep = 0
                if hKey = "netmask"   then hKeep = 0
                if hKey = "gateway"   then hKeep = 0
                if hKey = "nameserver" then hKeep = 0
                if hKeep then do
                    hLines.0 = hLines.0 + 1
                    hN = hLines.0
                    hLines.hN = hOne
                end
            end
            call close("r")
        end
    end

    if ~open("w", hCfgPath, "W") then return 0
    do i = 1 to hLines.0
        call writeln("w", hLines.i)
    end
    call writeln("w", "# emu68hatcher: managed by Network Config")
    if hMode = "dhcp" then
        call writeln("w", "configure=dhcp")
    else do
        call writeln("w", "address=" || hAddr)
        call writeln("w", "netmask=" || hMask)
        if hUseAmiTcp then do
            if hGateway ~= "" then call writeln("w", "gateway=" || hGateway)
            if exists(hDnsPath) then do
                if open("d", hDnsPath, "R") then do
                    do while ~eof("d")
                        hDnsLine = strip(readln("d"))
                        parse var hDnsLine hDnsKey hDnsValue
                        if upper(strip(hDnsKey)) = "NAMESERVER" then
                            call writeln("w", "nameserver=" || strip(hDnsValue))
                    end
                    call close("d")
                end
            end
        end
    end
    call close("w")
    return 1

UpdateAmiTcpDns: procedure
    parse arg hCfgPath, hDnsPath
    if ~exists(hCfgPath) then return 1

    hLines.0 = 0
    hStatic = 0
    if ~open("r", hCfgPath, "R") then return 0
    do while ~eof("r")
        hOne = readln("r")
        hCheck = strip(hOne)
        hKey = ""
        if hCheck ~= "" & left(hCheck, 1) ~= "#" then do
            parse var hCheck hKey "=" .
            hKey = strip(hKey)
        end
        if hKey = "address" then hStatic = 1
        if hKey ~= "nameserver" then do
            hLines.0 = hLines.0 + 1
            hN = hLines.0
            hLines.hN = hOne
        end
    end
    call close("r")

    if ~open("w", hCfgPath, "W") then return 0
    do i = 1 to hLines.0
        call writeln("w", hLines.i)
    end
    if hStatic & exists(hDnsPath) then do
        if open("d", hDnsPath, "R") then do
            do while ~eof("d")
                hDnsLine = strip(readln("d"))
                parse var hDnsLine hDnsKey hDnsValue
                if upper(strip(hDnsKey)) = "NAMESERVER" then
                    call writeln("w", "nameserver=" || strip(hDnsValue))
            end
            call close("d")
        end
    end
    call close("w")
    return 1

ReadDefaultRoute: procedure
    parse arg hCfgPath
    if ~exists(hCfgPath) then return ""
    if ~open("r", hCfgPath, "R") then return ""
    hGw = ""
    do while ~eof("r")
        hLine = strip(readln("r"))
        if hLine = "" then iterate
        if left(hLine, 1) = "#" then iterate
        parse upper var hLine hHead .
        if hHead = "DEFAULT" then do
            parse var hLine . hGw
            hGw = strip(hGw)
        end
    end
    call close("r")
    return hGw

WriteDefaultRoute: procedure
    parse arg hCfgPath, hGw

    /* drop any existing default route, keep everything else as-is */
    hLines.0 = 0
    if exists(hCfgPath) then do
        if open("r", hCfgPath, "R") then do
            do while ~eof("r")
                hOne = readln("r")
                hCheck = strip(hOne)
                hKeep = 1
                if hCheck ~= "" & left(hCheck, 1) ~= "#" then do
                    parse upper var hCheck hHead .
                    if hHead = "DEFAULT" then hKeep = 0
                end
                if hKeep then do
                    hLines.0 = hLines.0 + 1
                    hN = hLines.0
                    hLines.hN = hOne
                end
            end
            call close("r")
        end
    end

    if ~open("w", hCfgPath, "W") then return 0
    do i = 1 to hLines.0
        call writeln("w", hLines.i)
    end
    if hGw ~= "" then do
        call writeln("w", "# emu68hatcher: managed by Network Config")
        call writeln("w", "default " || hGw)
    end
    call close("w")
    return 1

DnsScreen:
    hCur = ""
    if exists(hDnsCfg) then do
        if open("d", hDnsCfg, "R") then do
            do while ~eof("d")
                hLine = strip(readln("d"))
                if hLine = "" then iterate
                if left(hLine, 1) = "#" then iterate
                parse var hLine hKw hRest
                if upper(strip(hKw)) = "NAMESERVER" then do
                    if hCur ~= "" then hCur = hCur || " "
                    hCur = hCur || strip(hRest)
                end
            end
            call close("d")
        end
    end

    hDnsPrompt = "DNS servers (space-separated, e.g. 8.8.8.8 1.1.1.1):"
    hNew = rtgetstring(hCur, hDnsPrompt, "DNS")
    if rtresult = 0 then return
    hNew = strip(hNew)

    if ~open("w", hDnsCfg, "W") then do
        call rtezrequest("Could not write " || hDnsCfg, "OK", "DNS")
        return
    end
    call writeln("w", "# emu68hatcher: managed by Network Config")
    do while hNew ~= ""
        parse var hNew hOne hNew
        hOne = strip(hOne)
        if hOne ~= "" then call writeln("w", "nameserver " || hOne)
    end
    call close("w")

    if hAmiTcp then do
        if ~UpdateAmiTcpDns(hWifipiCfg, hDnsCfg) then do
            call rtezrequest("Could not update " || hWifipiCfg, "OK", "DNS")
            return
        end
        if ~UpdateAmiTcpDns(hGenetCfg, hDnsCfg) then do
            call rtezrequest("Could not update " || hGenetCfg, "OK", "DNS")
            return
        end
    end

    call rtezrequest("DNS servers saved.", "OK", "DNS")
    return

BootToggleScreen:
    if exists(hBootDisable) then
        hPrompt = "Connect at boot is currently OFF." || '0a'x || "Turn it ON?"
    else
        hPrompt = "Connect at boot is currently ON." || '0a'x || "Turn it OFF?"

    if rtezrequest(hPrompt, "Yes|No", "Connect at boot") = 0 then return

    if exists(hBootDisable) then do
        "Delete " || hBootDisable || " QUIET >NIL:"
        call rtezrequest("Connect at boot: ON.", "OK", "Connect at boot")
    end
    else do
        if open("f", hBootDisable, "W") then do
            call writeln("f", "; emu68hatcher: network skipped at boot")
            call close("f")
            call rtezrequest("Connect at boot: OFF.", "OK", "Connect at boot")
        end
        else do
            hErr = "Could not write " || hBootDisable
            call rtezrequest(hErr, "OK", "Connect at boot")
        end
    end
    return
