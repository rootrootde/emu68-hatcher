"""mkdocs hook - generates docs/packages.md from the package yaml defs at build time"""

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent / "src" / "main" / "python"))
from emu68hatcher.data.catalog import load_catalog_source  # noqa: E402

_ROOT = Path(__file__).parent
_PKG_DIR = _ROOT / "src" / "main" / "python" / "emu68hatcher" / "data" / "packages"
_OUT = _ROOT / "docs" / "packages.md"

_GROUP_ORDER = [
    "System",
    "Drivers",
    "RTG",
    "Network",
    "Internet",
    "Commodities",
    "Applications",
    "Utilities",
    "Fonts",
    "Locale",
]
_ALL_VERSIONS = {"3.1", "3.2", "3.2.2.1", "3.2.3", "3.9"}

_HEADER = """\
# Packages

Generated from the package definitions used by the app. **always** is required,
**preselected** is on by default, and **optional** starts off.
Dependencies are installed when a selected package needs them. The Software
tab shows the reason. Minimal keeps the OS, RTG and FirstBoot tools;
see [Usage](usage.md) for its effect on software and networking.

"""


def _tier(pkg: dict) -> tuple[int, str]:
    if pkg.get("mandatory"):
        return 0, "always"
    if pkg.get("default"):
        return 1, "preselected"
    return 2, "optional"


def _source_cell(pkg: dict, packages: dict) -> str:
    if pkg.get("archive_package"):
        return _source_cell(packages[pkg["archive_package"]], packages)
    dl = pkg.get("download") or {}
    src = dl.get("source")
    if src == "aminet":
        cell = f"[Aminet](https://aminet.net/{dl.get('path', '')})"
    elif src == "github":
        cell = f"[GitHub](https://github.com/{dl.get('repo', '')})"
    elif src == "web":
        url = dl.get("url", "")
        cell = f"[{urlparse(url).netloc or 'web'}]({url})"
    elif src == "local":
        cell = "bundled"
    else:
        locale_like = pkg.get("group") == "Locale" or pkg.get("name", "").startswith("os_install")
        cell = "install media" if locale_like else "built in"
    if pkg.get("purchase_url"):
        cell += f" - [full version]({pkg['purchase_url']})"
    return cell


def _description_cell(pkg: dict) -> str:
    desc = (pkg.get("description") or "").replace("|", "\\|").strip()
    notes = []
    versions = {str(v) for v in (pkg.get("versions") or [])}
    if versions and versions != _ALL_VERSIONS:
        notes.append("Workbench " + "/".join(sorted(versions)) + " only")
    if pkg.get("emu68_versions"):
        notes.append("only with Emu68 " + "/".join(str(v) for v in pkg["emu68_versions"]))
    if notes:
        desc += f" *({'; '.join(notes)})*"
    return desc


def _render() -> str:
    by_group: dict[str, list[dict]] = {}
    packages = load_catalog_source(_PKG_DIR).model_dump(mode="json", by_alias=True)["packages"]
    for pkg in packages.values():
        by_group.setdefault(pkg.get("group", "Other"), []).append(pkg)

    order = _GROUP_ORDER + sorted(g for g in by_group if g not in _GROUP_ORDER)

    out = [_HEADER]
    for group in order:
        pkgs = by_group.get(group)
        if not pkgs:
            continue
        pkgs.sort(key=lambda p: (_tier(p)[0], (p.get("friendly_name") or p["name"]).lower()))
        out.append(f"## {group}\n\n")
        out.append("| Package | Installed | Description | Source |\n")
        out.append("|---|---|---|---|\n")
        for pkg in pkgs:
            name = pkg.get("friendly_name") or pkg["name"]
            out.append(
                f"| **{name}** | {_tier(pkg)[1]} "
                f"| {_description_cell(pkg)} | {_source_cell(pkg, packages)} |\n"
            )
        out.append("\n")
    return "".join(out)


def on_pre_build(config, **kwargs):
    content = _render()
    # only write on change, else mkdocs serve rebuilds in a loop
    if not _OUT.exists() or _OUT.read_text(encoding="utf-8") != content:
        _OUT.write_text(content, encoding="utf-8")
