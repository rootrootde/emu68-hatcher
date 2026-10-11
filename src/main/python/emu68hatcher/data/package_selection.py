"""Shared software choices for the GUI and builder."""

from emu68hatcher.config.schema import NetworkStack
from emu68hatcher.data.package_loader import load_all_bundles, load_all_packages
from emu68hatcher.data.package_resolver import Resolution, resolve
from emu68hatcher.data.themes import get_workbench_theme


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
    theme_name: str = "default",
) -> Resolution:
    states = software_defaults() | choices
    if choices.get("mui5") and not choices.get("mui38"):
        states["mui38"] = False
    stacks = {stack.value.lower() for stack in NetworkStack}
    requested = {name for name, enabled in states.items() if enabled and name not in stacks}
    disabled = {name for name, enabled in states.items() if not enabled and name not in stacks}
    reasons = {}
    if network_stack:
        requested.add(network_stack.value.lower())
        reasons[network_stack.value.lower()] = "Network tab"
    theme = get_workbench_theme(theme_name)
    if theme:
        requested.update(theme.required_packages)
        disabled.difference_update(theme.required_packages)
        reasons.update(dict.fromkeys(theme.required_packages, "Workbench theme"))
    result = resolve(requested, disabled, kickstart_version, emu68_version)
    result.selection_reasons = reasons
    for name in reasons.keys() - result.selected:
        result.unsatisfiable[name] = [reasons[name]]
    return result
