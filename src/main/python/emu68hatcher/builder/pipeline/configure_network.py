"""TCP/IP stack and wireless configuration."""

from __future__ import annotations

import base64
import hashlib
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.schema import NetworkStack
from emu68hatcher.data.package_loader import get_local_packages_dir, get_package_by_name

if TYPE_CHECKING:
    from emu68hatcher.builder.workflow import BuildWorkflow

_MANAGED_TAG = "# emu68hatcher: managed by Network Config"
_MIAMI_PROFILE_HASHES = {
    "Genet.default": "2c0798ace1242d2023921cdcb137c59a43c288c253bd2f3e50c3fe872fad543e",
    "Genet.default.info": "16bb164496e0103d283012f96385f40a336cf99f14dbf4dba611124af76bce12",
    "Uaenet.default": "d704a8c7005146dac7c03d8c19138d2ea0151a89630f2de299855cfc6102e22a",
    "Uaenet.default.info": "660fa90fd0342b7d873ccf99bcca0bd044e422283c6366c3d18a34118e4b46d3",
    "Wifipi.default": "7e4f0047d17e35a71f6a7bf54d0a08e34f00df9e74e5e82718fa2182613cfd9e",
    "Wifipi.default.info": "48d1ba993376ebead20041845c45e4d30ebdc94aa8ce45082ce5b63965db0c19",
}
_MIAMI_KEY_SIZES = {"MIAMI.KEY1": 2048, "MIAMI.KEY2": 2048, "MIAMIDX.KEY": 4096}


def generate_wireless_prefs(ssid: str, password: str) -> str:
    body = f'   ssid="{_wpa_escape(ssid)}"\n'
    body += f'   psk="{_wpa_escape(password)}"\n' if password else "   key_mgmt=NONE\n"
    body += "   scan_ssid=1\n"
    return f"network={{\n{body}}}\n"


def configure_network(workflow: BuildWorkflow, boot_staging: Path) -> None:
    if workflow.config.network_stack == NetworkStack.MIAMIDX:
        _configure_miamidx(workflow, boot_staging)
        return
    network = workflow.config.network
    amitcp = workflow.config.network_stack == NetworkStack.AMITCP_NG
    devs = boot_staging / "Devs"
    for name, settings in (("genet", network.ethernet), ("wifipi", network.wifi)):
        path = devs / "NetInterfaces" / name
        if not path.exists():
            workflow.logger.warning(f"NetInterfaces/{name} not staged; skipping its IP config")
            continue
        _write_netinterface(
            path,
            settings.mode.value,
            settings.address,
            settings.netmask,
            gateway=network.gateway if amitcp else None,
            dns_servers=network.dns_servers if amitcp else None,
        )

    if network.gateway:
        _write_default_route(devs / "Internet" / "routes", network.gateway)
    if network.dns_servers:
        _write_name_resolution(devs / "Internet" / "name_resolution", network.dns_servers)

    if amitcp:
        package = get_package_by_name("amitcp_ng")
        marker = "$VER: AmiTCP_NG"
        if package and package.download and package.download.tag:
            marker += f" {package.download.tag.removeprefix('v')}"
        _write_lines(boot_staging / "Libs" / "AmiTCP_NG.version", [marker])
    label = "AmiTCP_NG" if amitcp else "network"
    workflow.logger.info(
        f"Configured {label}: ethernet={network.ethernet.mode.value} "
        f"wifi={network.wifi.mode.value} gateway={network.gateway or '-'} "
        f"dns={network.dns_servers or '-'}"
    )


def _configure_miamidx(workflow: BuildWorkflow, boot_staging: Path) -> None:
    source_dir = get_local_packages_dir() / "System" / "Programs" / "Miami"
    target_dir = boot_staging / "Programs" / "Miami"
    target_dir.mkdir(parents=True, exist_ok=True)

    for filename, expected_hash in _MIAMI_PROFILE_HASHES.items():
        encoded_path = source_dir / f"{filename}.b64"
        try:
            encoded = "".join(encoded_path.read_text(encoding="ascii").split())
            profile = base64.b64decode(encoded, validate=True)
        except (OSError, ValueError) as exc:
            raise BuildError(f"MiamiDX profile is invalid: {encoded_path.name}: {exc}") from exc
        if hashlib.sha256(profile).hexdigest() != expected_hash:
            raise BuildError(f"MiamiDX profile checksum mismatch: {encoded_path.name}")
        (target_dir / filename).write_bytes(profile)

    copied_keys = _copy_miamidx_keys(
        workflow.config.miamidx_key_directory,
        target_dir,
    )
    key_status = ", registration keys installed" if copied_keys else ""
    workflow.logger.info(f"Configured MiamiDX DHCP profiles{key_status}")


def validate_miamidx_key_directory(source_dir: Path | None) -> dict[str, Path]:
    if source_dir is None:
        return {}
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise BuildError(f"MiamiDX keys folder not found: {source_dir}")

    entries = {entry.name.upper(): entry for entry in source_dir.iterdir() if entry.is_file()}
    valid: dict[str, Path] = {}
    problems: list[str] = []
    for filename, expected_size in _MIAMI_KEY_SIZES.items():
        source = entries.get(filename)
        if source is None:
            problems.append(f"missing {filename}")
            continue
        actual_size = source.stat().st_size
        if actual_size != expected_size:
            problems.append(f"{source.name} is {actual_size} bytes (expected {expected_size})")
            continue
        valid[filename] = source

    if problems:
        raise BuildError(
            "MiamiDX registration needs MIAMI.KEY1, MIAMI.KEY2, and MIAMIDX.KEY, "
            "or no keys folder. " + "; ".join(problems)
        )
    return valid


def _copy_miamidx_keys(source_dir: Path | None, target_dir: Path) -> int:
    sources = validate_miamidx_key_directory(source_dir)
    for filename, source in sources.items():
        shutil.copyfile(source, target_dir / filename)
    return len(sources)


def _wpa_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_netinterface(
    path: Path,
    mode: str,
    address: str | None,
    netmask: str | None,
    *,
    gateway: str | None = None,
    dns_servers: list[str] | None = None,
) -> None:
    kept: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="iso-8859-1").splitlines():
            stripped = line.strip()
            key = stripped.split("=", 1)[0].strip().lower() if stripped[:1] not in ("", "#") else ""
            if key not in ("configure", "address", "netmask", "gateway", "nameserver"):
                kept.append(line)
    kept.append(_MANAGED_TAG)
    if mode == "static" and address and netmask:
        kept += [f"address={address}", f"netmask={netmask}"]
        if gateway:
            kept.append(f"gateway={gateway}")
        kept.extend(f"nameserver={server}" for server in dns_servers or [])
    else:
        kept.append("configure=dhcp")
    _write_lines(path, kept)


def _write_default_route(path: Path, gateway: str) -> None:
    kept: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="iso-8859-1").splitlines():
            stripped = line.strip()
            if stripped[:1] not in ("", "#") and stripped.split()[0].lower() == "default":
                continue
            kept.append(line)
    _write_lines(path, [*kept, _MANAGED_TAG, f"default {gateway}"])


def _write_name_resolution(path: Path, dns_servers: list[str]) -> None:
    _write_lines(path, [_MANAGED_TAG, *(f"nameserver {ip}" for ip in dns_servers)])


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="iso-8859-1", newline="\n")
