"""JSON config file load/save"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from emu68hatcher.config.schema import CURRENT_CONFIG_VERSION, BuildConfig


class ConfigurationError(Exception):
    """raised when config loading or validation fails"""

    pass


def _migrate_1_0(data: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(data)
    migrated.pop("metadata", None)

    install_media = migrated.get("install_media")
    if isinstance(install_media, dict):
        install_media.pop("version", None)

    if not migrated.get("asset_directories"):
        paths = []
        kickstart = migrated.get("kickstart")
        if isinstance(kickstart, dict) and kickstart.get("rom_directory"):
            paths.append(kickstart["rom_directory"])
        if isinstance(install_media, dict) and install_media.get("directory"):
            paths.append(install_media["directory"])
        migrated["asset_directories"] = list(dict.fromkeys(paths))

    migrated["version"] = "1.1.0"
    return migrated


def _migrate_1_1(data: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(data)
    ags = migrated.get("ags_import")
    if isinstance(ags, dict) and "content_device" in ags:
        device = ags.pop("content_device")
        ags.pop("scope", None)
        ags.update(
            enabled=True,
            components={"whdload": True, "games": False, "emulators": False, "media": False},
            allocation_state="pending",
            legacy_content_device=device,
        )
    migrated["version"] = "1.2.0"
    return migrated


def _migrate_1_2(data: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(data)
    ags = migrated.get("ags_import")
    if isinstance(ags, dict):
        components = ags.setdefault("components", {})
        components["work"] = components.pop("emulators", True)
        ags["allocation_state"] = "pending"
        ags["migration_notice"] = (
            "The former Emulators selection now proposes the entire Work partition, "
            "including applications. Check the AGS content and available space. "
            "Former AGS partitions remain ordinary partitions; remove or rename them explicitly."
        )
    for mbr in (migrated.get("partitions") or {}).get("layout", []):
        for part in mbr.get("amiga_partitions") or []:
            part.pop("ags_reservation", None)
    migrated["version"] = CURRENT_CONFIG_VERSION
    return migrated


def migrate_config_data(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a JSON object")

    version = raw.get("version", "1.0.0")
    if not isinstance(version, str):
        raise ConfigurationError("Configuration version must be a string")
    if version == CURRENT_CONFIG_VERSION:
        return deepcopy(raw)
    if version == "1.0.0":
        return _migrate_1_2(_migrate_1_1(_migrate_1_0(raw)))
    if version == "1.1.0":
        return _migrate_1_2(_migrate_1_1(raw))
    if version == "1.2.0":
        return _migrate_1_2(raw)
    raise ConfigurationError(
        f"Unsupported configuration version {version!r}; expected {CURRENT_CONFIG_VERSION}"
    )


def load_config(path: str | Path) -> BuildConfig:
    """load a build configuration from a JSON file"""
    path = Path(path)

    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    if not path.is_file():
        raise ConfigurationError(f"Path is not a file: {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigurationError(f"Failed to read configuration file: {e}") from e

    try:
        raw = json.loads(content)
        return BuildConfig.model_validate(migrate_config_data(raw))
    except ConfigurationError:
        raise
    except Exception as e:
        raise ConfigurationError(f"Invalid configuration: {e}") from e


def save_config(
    config: BuildConfig,
    path: str | Path,
) -> None:
    """save a build configuration to a JSON file"""
    path = Path(path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = config.model_dump_json(indent=2)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        raise ConfigurationError(f"Failed to save configuration: {e}") from e
