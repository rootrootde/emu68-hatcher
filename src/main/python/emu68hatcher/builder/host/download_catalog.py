"""download catalog - builds DownloadItems from package defs, GitHub releases, and tools.yaml"""

import json
import re
import urllib.request

from emu68hatcher.builder.host.downloads import DownloadItem
from emu68hatcher.data.emu68_releases import download_table as emu68_download_table
from emu68hatcher.data.package_loader import (
    get_mandatory_packages as get_mandatory_package_objs,
)
from emu68hatcher.data.package_loader import (
    get_package_by_name,
)
from emu68hatcher.data.package_schema import SourceType
from emu68hatcher.utils.logging import get_logger

# owner/repo with limited punctuation - blocks path-traversal and query-string smuggling from YAML
_GITHUB_REPO_RE = re.compile(r"^[\w][\w.-]*/[\w][\w.-]*$")


def get_required_startup_files() -> list[DownloadItem]:
    """resolve pfs3aio FS handler from tools.yaml. returns single-item list (historical shape). empty if unresolvable"""
    from emu68hatcher.builder.host.tools import resolve_tool_download

    info = resolve_tool_download("pfs3aio")
    if not info or not info.get("url"):
        return []

    return [
        DownloadItem(
            name="pfs3aio",
            url=info["url"],
            filename=info.get("filename") or "pfs3aio.lha",
            expected_hash=info.get("hash") or None,
            extract=True,
            extract_file="pfs3aio",
        )
    ]


def _resolve_github_download(api_url: str, expected_filename: str, logger) -> str | None:
    """resolve GitHub release asset URL by exact filename - no substitution (hostile-mirror guard)"""
    try:
        if api_url.endswith("/releases"):
            api_url = api_url + "/latest"

        request = urllib.request.Request(api_url, headers={"User-Agent": "Emu68 Hatcher/1.0"})
        with urllib.request.urlopen(request, timeout=30.0) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        # surface, don't whisper - caller expects None on no-match, not on api errors
        logger.error(f"GitHub API call failed for {api_url}: {e}")
        return None

    for asset in release.get("assets", []):
        if asset.get("name", "").lower() == expected_filename.lower():
            download_url = asset.get("browser_download_url")
            if download_url:
                logger.debug(f"Resolved GitHub asset: {asset['name']} -> {download_url}")
                return download_url

    logger.error(f"GitHub release has no asset named {expected_filename!r}; refusing to substitute")
    return None


def _aminet_item(pkg, pkg_name, filename, expected_hash, logger) -> DownloadItem | None:
    aminet_path = pkg.download.path
    if not aminet_path:
        logger.warning(f"Aminet package {pkg_name} has no download path; skipping")
        return None
    url = f"https://aminet.net/{aminet_path}"
    logger.info(f"Queued Aminet download: {pkg_name} from {url}")
    return DownloadItem(
        name=pkg_name,
        url=url,
        filename=filename,
        expected_hash=expected_hash or None,
        extract=True,
    )


def _github_item(pkg, pkg_name, filename, expected_hash, logger) -> DownloadItem | None:
    repo = pkg.download.repo
    if not repo:
        return None
    if not _GITHUB_REPO_RE.match(repo):
        logger.error(f"refusing malformed GitHub repo for {pkg_name}: {repo!r}")
        return None
    if pkg.download.tag:
        api_url = f"https://api.github.com/repos/{repo}/releases/tags/{pkg.download.tag}"
    else:
        api_url = f"https://api.github.com/repos/{repo}/releases"
    download_url = _resolve_github_download(api_url, filename, logger)
    if not download_url:
        logger.warning(f"Failed to resolve GitHub URL for {pkg_name}")
        return None
    logger.info(f"Queued GitHub download: {pkg_name} from {download_url}")
    return DownloadItem(
        name=pkg_name,
        url=download_url,
        filename=filename,
        expected_hash=expected_hash or None,
        extract=True,
    )


def _web_item(pkg, pkg_name, filename, expected_hash, logger) -> DownloadItem | None:
    url = pkg.download.url
    if not url:
        return None
    backup_url = pkg.download.backup_url
    logger.info(f"Queued web download: {pkg_name} from {url}")
    return DownloadItem(
        name=pkg_name,
        url=url,
        filename=filename,
        expected_hash=expected_hash or None,
        extract=True,
        mirrors=[backup_url] if backup_url else [],
    )


_SOURCE_HANDLERS = {
    SourceType.AMINET: _aminet_item,
    SourceType.GITHUB: _github_item,
    SourceType.WEB: _web_item,
}


def get_package_downloads(package_names: list[str]) -> list[DownloadItem]:
    """build DownloadItems from package YAML defs - handles aminet, github, web, local sources"""
    import logging
    from pathlib import Path

    logger = logging.getLogger("emu68hatcher")

    items = []
    seen_filenames: set[str] = set()  # avoid duplicate downloads

    for pkg_name in package_names:
        pkg = get_package_by_name(pkg_name)

        if not pkg:
            logger.debug(f"Package not found: {pkg_name}")
            continue

        if not pkg.download:
            logger.debug(f"Package has no download info: {pkg_name}")
            continue

        source = pkg.download.source

        if source == SourceType.LOCAL:
            logger.debug(f"Skipping local source for {pkg_name}")
            continue

        if pkg.download.filename:
            filename = pkg.download.filename
        elif pkg.download.path:
            filename = Path(pkg.download.path).name
        elif pkg.download.url:
            filename = pkg.download.url.split("/")[-1]
        else:
            filename = f"{pkg.name}.lha"

        if filename.lower() in seen_filenames:
            logger.debug(f"Skipping duplicate download: {filename}")
            continue
        seen_filenames.add(filename.lower())

        expected_hash = pkg.download.hash

        handler = _SOURCE_HANDLERS.get(source)
        if handler is None:
            logger.debug(f"Unknown source type for {pkg_name}: {source}")
            continue

        item = handler(pkg, pkg_name, filename, expected_hash, logger)
        if item:
            items.append(item)

    logger.info(f"Total packages queued for download: {len(items)}")
    return items


def downloadable_mandatory_names(
    kickstart_version: str = "3.1", emu68_version: str | None = None
) -> list[str]:
    """names of mandatory=true packages with downloadable sources"""
    import logging

    logger = logging.getLogger("emu68hatcher")

    mandatory = []
    seen = set()

    mandatory_pkgs = get_mandatory_package_objs(kickstart_version, emu68_version)

    for pkg in mandatory_pkgs:
        if not pkg.download:
            continue

        if pkg.download.source == SourceType.LOCAL:
            continue

        name = pkg.name
        if name and name.lower() not in seen:
            seen.add(name.lower())
            mandatory.append(name)
            logger.debug(f"Found mandatory package: {name}")

    return mandatory


# per-version asset map: each entry is (state-key, github-release-asset-name)
EMU68_RELEASES: dict[str, dict] = emu68_download_table()


def get_emu68_boot_files(version: str) -> list[DownloadItem]:
    """DownloadItems for every Emu68 PiStorm boot variant of the given release"""
    from emu68hatcher.builder.errors import BuildError

    logger = get_logger()

    if version not in EMU68_RELEASES:
        raise BuildError(f"Unknown Emu68 version '{version}'. Known: {', '.join(EMU68_RELEASES)}")

    rel = EMU68_RELEASES[version]
    api_url = f"https://api.github.com/repos/michalsc/Emu68/releases/tags/{rel['tag']}"

    try:
        request = urllib.request.Request(api_url, headers={"User-Agent": "Emu68 Hatcher/1.0"})
        with urllib.request.urlopen(request, timeout=30.0) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise BuildError(f"Could not fetch Emu68 {rel['tag']} release info from GitHub: {e}") from e

    asset_map = {}
    for asset in release.get("assets", []):
        asset_name = asset.get("name", "")
        download_url = asset.get("browser_download_url")
        if download_url:
            asset_map[asset_name] = download_url

    items = []
    missing = []
    for item_name, zip_filename in rel["zips"]:
        if zip_filename in asset_map:
            # version-prefix to avoid cache collisions between releases
            cached_name = f"emu68-{version}-{zip_filename}"
            items.append(
                DownloadItem(
                    name=item_name,
                    url=asset_map[zip_filename],
                    filename=cached_name,
                    extract=True,
                )
            )
            logger.debug(f"Found Emu68 {version} variant: {zip_filename}")
        else:
            missing.append(zip_filename)

    for item_name, asset_filename in rel.get("extras", []):
        if asset_filename in asset_map:
            cached_name = f"emu68-{version}-{asset_filename}"
            items.append(
                DownloadItem(
                    name=item_name,
                    url=asset_map[asset_filename],
                    filename=cached_name,
                    extract=False,
                )
            )
            logger.debug(f"Found Emu68 {version} extra asset: {asset_filename}")
        else:
            missing.append(asset_filename)

    if missing:
        raise BuildError(
            f"Emu68 {rel['tag']} release is missing required boot variant(s): "
            + ", ".join(missing)
            + ". both PiStorm variants must be present, otherwise config.txt GPIO branches"
            " select a kernel that isn't on disk"
        )

    return items
