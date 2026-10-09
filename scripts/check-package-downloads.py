#!/usr/bin/env python3
"""Check remote package files against their configured checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "main" / "python"))

from emu68hatcher.data.catalog import load_catalog_source, read_catalog_yaml  # noqa: E402
from emu68hatcher.data.package_schema import DownloadInfo, SourceType  # noqa: E402

_ARTIFACT_LOCK = ROOT / "updates" / "amiga-artifacts.lock.yaml"

_GITHUB_REPO_RE = re.compile(r"^[\w][\w.-]*/[\w][\w.-]*$")
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactPin:
    """reviewed native pin from updates/amiga-artifacts.lock.yaml"""

    url: str
    size: int
    sha256: str
    md5: str


@dataclass(frozen=True)
class PackageSpec:
    name: str
    download: DownloadInfo
    pin: ArtifactPin | None = None


@dataclass(frozen=True)
class Digest:
    md5: str
    sha256: str
    size: int


@dataclass(frozen=True)
class CheckResult:
    name: str
    source: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "current"


def _load_pins(lock_path: Path) -> dict[str, ArtifactPin]:
    if not lock_path.is_file():
        return {}
    artifacts = (read_catalog_yaml(lock_path) or {}).get("artifacts") or {}
    return {
        name: ArtifactPin(entry["url"], int(entry["size"]), entry["sha256"], entry["md5"])
        for name, entry in artifacts.items()
    }


def _load_specs(packages_dir: Path, lock_path: Path = _ARTIFACT_LOCK) -> list[PackageSpec]:
    catalog = load_catalog_source(packages_dir)
    pins = _load_pins(lock_path)
    unknown = pins.keys() - catalog.packages.keys()
    if unknown:
        raise ValueError(f"artifact lock names unknown packages: {', '.join(sorted(unknown))}")
    return [
        PackageSpec(p.name, p.download, pins.get(p.name))
        for p in catalog.packages.values()
        if p.download and p.download.source != SourceType.LOCAL
    ]


def _request(url: str, timeout: float, *, github_api: bool = False):
    headers = {"User-Agent": "Emu68 Hatcher package checker/1.0"}
    token = os.environ.get("GH_TOKEN") if github_api else None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if github_api:
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def _github_release(repo: str, selector: str, timeout: float) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/{selector}"
    with _request(url, timeout, github_api=True) as response:
        return json.loads(response.read().decode("utf-8"))


def _github_asset(spec: PackageSpec, timeout: float) -> tuple[str, str | None]:
    download = spec.download
    repo = download.repo or ""
    if not _GITHUB_REPO_RE.fullmatch(repo):
        raise ValueError(f"invalid GitHub repo: {repo!r}")
    if not download.filename:
        raise ValueError("GitHub package has no filename")

    latest_tag = None
    if download.tag:
        tag = urllib.parse.quote(download.tag, safe="")
        release = _github_release(repo, f"tags/{tag}", timeout)
        if download.check_latest_release:
            latest_tag = _github_release(repo, "latest", timeout).get("tag_name")
    else:
        release = _github_release(repo, "latest", timeout)

    for asset in release.get("assets", []):
        if asset.get("name", "").lower() == download.filename.lower():
            url = asset.get("browser_download_url")
            if url:
                return url, latest_tag
    raise ValueError(f"release has no asset named {download.filename}")


def _download_url(spec: PackageSpec, timeout: float) -> tuple[str, str | None]:
    download = spec.download
    if download.source == SourceType.AMINET:
        if not download.path:
            raise ValueError("Aminet package has no path")
        return f"https://aminet.net/{download.path.lstrip('/')}", None
    if download.source == SourceType.WEB:
        if not download.url:
            raise ValueError("web package has no URL")
        return download.url, None
    if download.source == SourceType.GITHUB:
        return _github_asset(spec, timeout)
    raise ValueError(f"unsupported source: {download.source.value}")


def _remote_digest(url: str, timeout: float) -> Digest:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    size = 0
    with _request(url, timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > _MAX_DOWNLOAD_BYTES:
            raise ValueError(f"download is larger than {_MAX_DOWNLOAD_BYTES} bytes")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_DOWNLOAD_BYTES:
                raise ValueError(f"download is larger than {_MAX_DOWNLOAD_BYTES} bytes")
            md5.update(chunk)
            sha256.update(chunk)
    return Digest(md5.hexdigest().upper(), sha256.hexdigest(), size)


def _pin_finding(pin: ArtifactPin, expected_md5: str | None, digest: Digest) -> str | None:
    """why the reviewed native pin no longer describes the archive, if it does not"""
    if expected_md5 and expected_md5.lower() != pin.md5:
        return f"YAML MD5 {expected_md5.upper()} differs from the reviewed lock MD5 {pin.md5}"
    if digest.sha256 != pin.sha256 or digest.size != pin.size:
        return (
            f"{pin.url}: got SHA-256 {digest.sha256} ({digest.size} bytes), "
            f"lock says {pin.sha256} ({pin.size} bytes)"
        )
    return None


def _check_package(spec: PackageSpec, timeout: float) -> CheckResult:
    source = spec.download.source.value
    try:
        url, latest_tag = _download_url(spec, timeout)
        digest = _remote_digest(url, timeout)
        pinned = None
        if spec.pin is not None:
            pinned = digest if spec.pin.url == url else _remote_digest(spec.pin.url, timeout)
    except urllib.error.HTTPError as error:
        return CheckResult(
            spec.name, source, "download failed", f"HTTP {error.code} at {error.url}"
        )
    except Exception as error:
        detail = str(error) or type(error).__name__
        return CheckResult(spec.name, source, "download failed", detail)

    observed_hash, size = digest.md5, digest.size
    expected_hash = spec.download.hash
    if spec.pin is not None and pinned is not None:
        finding = _pin_finding(spec.pin, expected_hash, pinned)
        if finding:
            return CheckResult(spec.name, source, "pinned artifact changed", finding)
    if not expected_hash:
        return CheckResult(
            spec.name,
            source,
            "no checksum",
            f"current MD5 is {observed_hash} ({size} bytes)",
        )
    if observed_hash.lower() != expected_hash.lower():
        return CheckResult(
            spec.name,
            source,
            "content changed",
            f"got {observed_hash} ({size} bytes), expected {expected_hash.upper()}",
        )
    if latest_tag and latest_tag != spec.download.tag:
        return CheckResult(
            spec.name,
            source,
            "update available",
            f"configured {spec.download.tag}, latest release is {latest_tag}",
        )
    detail = f"MD5 {observed_hash}"
    if spec.pin is not None:
        detail += f", pinned SHA-256 {spec.pin.sha256[:12]}"
    return CheckResult(spec.name, source, "current", detail)


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _report(results: list[CheckResult], source_counts: Counter) -> str:
    findings = [result for result in results if not result.ok]
    lines = [
        "# Package download check",
        "",
        f"Checked {len(results)} remote package downloads.",
        "",
    ]
    if findings:
        lines.extend(
            [
                "## Findings",
                "",
                "| Package | Source | Result | Details |",
                "| --- | --- | --- | --- |",
            ]
        )
        for result in findings:
            lines.append(
                f"| {result.name} | {result.source} | {result.status} | "
                f"{_markdown_cell(result.detail)} |"
            )
        lines.extend(
            [
                "",
                "Changed files are not accepted automatically. Check their contents and install "
                "rules before updating the checksum or release tag, and the reviewed pin in "
                "updates/amiga-artifacts.lock.yaml for packages the Amiga tool installs.",
                "",
            ]
        )
    else:
        lines.extend(["No changed files, newer GitHub releases, or broken links found.", ""])
    coverage = ", ".join(f"{source}: {count}" for source, count in sorted(source_counts.items()))
    lines.extend(["## Coverage", "", coverage + ".", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--packages-dir",
        type=Path,
        default=ROOT / "src" / "main" / "python" / "emu68hatcher" / "data" / "packages",
    )
    parser.add_argument("--lock", type=Path, default=_ARTIFACT_LOCK)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    try:
        specs = _load_specs(args.packages_dir, args.lock)
        if args.package:
            selected = set(args.package)
            unknown = selected - {spec.name for spec in specs}
            if unknown:
                raise ValueError(f"unknown remote packages: {', '.join(sorted(unknown))}")
            specs = [spec for spec in specs if spec.name in selected]
        results = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            pending = {executor.submit(_check_package, spec, args.timeout): spec for spec in specs}
            for future in as_completed(pending):
                results.append(future.result())
        results.sort(key=lambda result: result.name)
        counts = Counter(spec.download.source.value for spec in specs)
        report = _report(results, counts)
        status = 1 if any(not result.ok for result in results) else 0
    except Exception as error:
        report = f"# Package download check\n\nChecker failed: {error}\n"
        status = 2

    print(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8", newline="\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
