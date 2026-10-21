"""Shared software choices for the GUI and builder."""

from emu68hatcher.config.schema import NetworkStack
from emu68hatcher.data.package_loader import load_all_bundles, load_all_packages
from emu68hatcher.data.package_resolver import Resolution, resolve


def software_defaults() -> dict[str, bool]:
    bundles = load_all_bundles()
    stacks = {stack.value.lower() for stack in NetworkStack}
    return {
        pkg.name: bundles[pkg.bundle].default if pkg.bundle else pkg.default
        for pkg in load_all_packages()
        if not pkg.mandatory and pkg.group != "Locale" and pkg.name not in stacks
    }


def resolve_choices(
    choices: dict[str, bool],
    kickstart_version: str,
    emu68_version: str | None,
    network_stack: NetworkStack | None = None,
    *,
    ags_enabled: bool = False,
    rgb2rtg_enabled: bool = False,
) -> Resolution:
    states = software_defaults() | choices
    if choices.get("mui5") and not choices.get("mui38"):
        states["mui38"] = False
    reasons = {}
    if ags_enabled:
        reasons["whdload"] = "AGS"
    if rgb2rtg_enabled:
        reasons.update(toolsdaemon="RGB2RTG", picasso96="RGB2RTG")
    states.update(dict.fromkeys(reasons, True))
    stacks = {stack.value.lower() for stack in NetworkStack}
    requested = {name for name, enabled in states.items() if enabled and name not in stacks}
    if network_stack:
        requested.add(network_stack.value.lower())
        reasons[network_stack.value.lower()] = "Network tab"
    result = resolve(requested, kickstart_version, emu68_version)
    result.selection_reasons = reasons
    for name in reasons.keys() - result.selected:
        result.unsatisfiable[name] = [reasons[name]]
    return result


def missing_optional_adfs(
    rules: list,
    selected: set[str],
    icon_set: str,
    found: set[str],
    required: set[str],
) -> dict[str, list[str]]:
    """Map each missing ADF that a selected package's rule needs to those packages."""
    missing: dict[str, list[str]] = {}
    for rule in rules:
        if rule.mandatory or not rule.package or rule.package.lower() not in selected:
            continue
        if rule.icon_set and rule.icon_set.lower() != (icon_set or "").lower():
            continue
        if rule.adf in required or rule.adf in found:
            continue
        missing.setdefault(rule.adf, []).append(rule.package)
    return {adf: sorted(set(packages)) for adf, packages in sorted(missing.items())}
