"""download catalog - builds DownloadItems from package defs, GitHub releases, and tools.yaml"""

import json
import re
import urllib.parse
import urllib.request
from functools import cache

import yaml

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


def get_pfs3aio_download() -> DownloadItem | None:
    """pfs3aio FS handler from tools.yaml, None if unresolvable"""
    from emu68hatcher.builder.host.tools import resolve_tool_download

    info = resolve_tool_download("pfs3aio")
    if not info or not info.get("url"):
        return None

    return DownloadItem(
        name="pfs3aio",
        url=info["url"],
        filename=info.get("filename") or "pfs3aio.lha",
        expected_hash=info.get("hash") or None,
        extract=True,
        extract_file="pfs3aio",
    )


def _resolve_github_download(
    api_url: str, expected_filename: str, logger
) -> tuple[str | None, str]:
    """resolve GitHub release asset URL by exact filename; returns (url, failure reason)"""
    try:
        if api_url.endswith("/releases"):
            api_url = api_url + "/latest"

        request = urllib.request.Request(api_url, headers={"User-Agent": "Emu68 Hatcher/1.0"})
        with urllib.request.urlopen(request, timeout=30.0) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return None, f"GitHub release lookup failed ({api_url}): {e}"

    for asset in release.get("assets", []):
        if asset.get("name", "").lower() == expected_filename.lower():
            download_url = asset.get("browser_download_url")
            if download_url:
                logger.debug(f"Resolved GitHub asset: {asset['name']} -> {download_url}")
                return download_url, ""

    return None, f"GitHub release {api_url} has no asset named {expected_filename!r}"


_QueueResult = tuple[DownloadItem | None, str]


def _aminet_item(pkg, pkg_name, filename, expected_hash, logger) -> _QueueResult:
    aminet_path = pkg.download.path
    if not aminet_path:
        return None, "Aminet package has no download path in its YAML"
    url = f"https://aminet.net/{aminet_path}"
    logger.info(f"Queued Aminet download: {pkg_name} from {url}")
    item = DownloadItem(
        name=pkg_name,
        url=url,
        filename=filename,
        expected_hash=expected_hash or None,
        extract=True,
    )
    return item, ""


def _github_item(pkg, pkg_name, filename, expected_hash, logger) -> _QueueResult:
    repo = pkg.download.repo
    if not repo:
        return None, "GitHub package has no repo in its YAML"
    if not _GITHUB_REPO_RE.match(repo):
        return None, f"GitHub repo {repo!r} in its YAML is not in owner/name form"
    tag = pkg.download.tag
    if tag:
        download_url = (
            f"https://github.com/{repo}/releases/download/{tag}/{urllib.parse.quote(filename)}"
        )
    else:
        api_url = f"https://api.github.com/repos/{repo}/releases"
        download_url, reason = _resolve_github_download(api_url, filename, logger)
        if not download_url:
            return None, reason
    logger.info(f"Queued GitHub download: {pkg_name} from {download_url}")
    item = DownloadItem(
        name=pkg_name,
        url=download_url,
        filename=filename,
        expected_hash=expected_hash or None,
        extract=True,
    )
    return item, ""


def _web_item(pkg, pkg_name, filename, expected_hash, logger) -> _QueueResult:
    url = pkg.download.url
    if not url:
        return None, "web package has no url in its YAML"
    logger.info(f"Queued web download: {pkg_name} from {url}")
    item = DownloadItem(
        name=pkg_name,
        url=url,
        filename=filename,
        expected_hash=expected_hash or None,
        extract=True,
    )
    return item, ""


_SOURCE_HANDLERS = {
    SourceType.AMINET: _aminet_item,
    SourceType.GITHUB: _github_item,
    SourceType.WEB: _web_item,
}


def get_package_downloads(
    package_names: list[str],
) -> tuple[list[DownloadItem], dict[str, str]]:
    """DownloadItems from package YAML defs, plus name -> reason for downloadable ones not queued"""
    import logging
    from pathlib import Path

    logger = logging.getLogger("emu68hatcher")

    items = []
    unqueued: dict[str, str] = {}
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
            unqueued[pkg_name] = f"unsupported download source {source!r}"
            logger.warning(f"Cannot queue {pkg_name}: {unqueued[pkg_name]}")
            continue

        item, reason = handler(pkg, pkg_name, filename, expected_hash, logger)
        if item:
            items.append(item)
        else:
            unqueued[pkg_name] = reason
            logger.warning(f"Cannot queue {pkg_name}: {reason}")

    logger.info(f"Total packages queued for download: {len(items)}")
    return items, unqueued


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


@cache
def emu68_releases() -> dict[str, dict]:
    """per-version asset map: each asset is {role, filename, url, size, sha256}, pins may be None"""
    return emu68_download_table()


def get_emu68_boot_files(version: str) -> list[DownloadItem]:
    """DownloadItems for every Emu68 PiStorm boot variant of the given release"""
    from emu68hatcher.builder.errors import BuildError

    logger = get_logger()

    try:
        releases = emu68_releases()
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise BuildError(f"emu68_releases.yaml is invalid: {error}") from error
    if version not in releases:
        raise BuildError(f"Unknown Emu68 version '{version}'. Known: {', '.join(releases)}")

    rel = releases[version]
    assets = [(entry, True) for entry in rel.get("zips", [])]
    assets += [(entry, False) for entry in rel.get("extras", [])]

    items = []
    missing = []
    for asset, extract in assets:
        asset_filename = asset["filename"]
        if not asset["url"]:
            missing.append(asset_filename)
            continue
        # version-prefix to avoid cache collisions between releases
        cached_name = f"emu68-{version}-{asset_filename}"
        items.append(
            DownloadItem(
                name=asset["role"],
                url=asset["url"],
                filename=cached_name,
                expected_sha256=asset["sha256"],
                expected_size=asset["size"],
                extract=extract,
            )
        )
        logger.debug(f"Found Emu68 {version} asset: {asset_filename}")

    if missing:
        raise BuildError(
            f"emu68_releases.yaml has no download url for Emu68 {rel['tag']} asset(s): "
            + ", ".join(missing)
            + ". Add the GitHub release url for each. Both PiStorm variants must be present,"
            " otherwise config.txt GPIO branches select a kernel that isn't on disk"
        )

    return items
