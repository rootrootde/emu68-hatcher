"""YAML package and ADF-rule loader."""

from pathlib import Path

from emu68hatcher.data.catalog import get_catalog_snapshot, read_catalog_yaml
from emu68hatcher.data.package_schema import ADFRule, Bundle, Package, _group_rank

# bundled Amiga files + scripts
LOCAL_PACKAGES_DIR = Path(__file__).parent / "local_packages"


def get_local_packages_dir() -> Path:
    return LOCAL_PACKAGES_DIR


def load_package(yaml_path: Path) -> Package:
    """load a single package from a YAML file"""
    data = read_catalog_yaml(yaml_path)
    if not data:
        raise ValueError("empty package definition")
    return Package.model_validate(data)


def load_all_packages() -> list[Package]:
    return get_catalog_snapshot().packages()


def clear_package_caches() -> None:
    from emu68hatcher.data.catalog import clear_bundled_catalog_cache

    clear_bundled_catalog_cache()


def get_packages_for_version(
    kickstart_version: str, emu68_version: str | None = None
) -> list[Package]:
    """Return packages compatible with both selected versions."""
    packages = load_all_packages()
    compatible = [
        p
        for p in packages
        if p.matches_version(kickstart_version) and p.matches_emu68(emu68_version)
    ]

    # sort by group order, then by name
    return sorted(compatible, key=lambda p: (_group_rank(p), p.name))


def get_mandatory_packages(
    kickstart_version: str, emu68_version: str | None = None
) -> list[Package]:
    """packages that must be installed for a version (anything mandatory=True)"""
    packages = get_packages_for_version(kickstart_version, emu68_version)
    return [p for p in packages if p.mandatory]


def get_package_by_name(name: str) -> Package | None:
    """get a specific package by name"""
    return get_catalog_snapshot().package(name)


###########
# bundles #
###########


def load_all_bundles() -> dict[str, Bundle]:
    """load bundle definitions from reference/bundles.yaml, validate against packages"""
    return get_catalog_snapshot().bundles()


def get_bundles_for_version(
    kickstart_version: str, emu68_version: str | None = None
) -> list[Bundle]:
    """bundles with at least one member compatible with a Kickstart version"""
    bundles = load_all_bundles()
    compatible = {
        p.bundle
        for p in get_packages_for_version(kickstart_version, emu68_version)
        if p.bundle and not p.mandatory
    }

    return sorted(
        (bundles[bid] for bid in compatible),
        key=lambda b: (_group_rank(b), b.display_name),
    )


def get_bundle_members(
    bundle_id: str, kickstart_version: str, emu68_version: str | None = None
) -> list[Package]:
    """member packages of a bundle compatible with the given Kickstart version"""
    return [
        p
        for p in get_packages_for_version(kickstart_version, emu68_version)
        if p.bundle == bundle_id and not p.mandatory
    ]


#####################
# ADF rules loading #
#####################


def load_adf_rules() -> dict[str, list[ADFRule]]:
    """load all ADF extraction rules from adf_rules.yaml."""
    return get_catalog_snapshot().adf_rules()


def get_adf_rules_for_version(kickstart_version: str) -> list[ADFRule]:
    """get ADF extraction rules for a specific Kickstart version"""
    all_rules = load_adf_rules()

    # try exact version match first
    if kickstart_version in all_rules:
        return all_rules[kickstart_version]

    # fall back to major.minor version (e.g., "3.2.3" -> "3.2")
    parts = kickstart_version.split(".")
    if len(parts) >= 2:
        base_version = f"{parts[0]}.{parts[1]}"
        if base_version in all_rules:
            return all_rules[base_version]

    return []


def get_filtered_adf_rules(
    kickstart_version: str,
    enabled_packages: set[str],
    icon_set: str = "Standard",
) -> list[ADFRule]:
    """get ADF rules filtered by enabled packages and icon set"""
    rules = get_adf_rules_for_version(kickstart_version)
    filtered = []

    for rule in rules:
        # check icon set filter
        if rule.icon_set and rule.icon_set.lower() != icon_set.lower():
            continue

        # check package association
        if rule.package:
            if rule.mandatory:
                # mandatory package - always include
                filtered.append(rule)
            elif rule.package.lower() in enabled_packages:
                # optional package that user enabled
                filtered.append(rule)
            # else: optional package not enabled, skip
        else:
            # no package association - always include
            filtered.append(rule)

    # sort by sequence
    return sorted(filtered, key=lambda r: r.sequence)
