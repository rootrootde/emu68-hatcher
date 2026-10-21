"""Download URLs derived from package download entries, without network access."""

import re
import urllib.parse

# owner/repo with limited punctuation - blocks path-traversal and query-string smuggling from YAML
_GITHUB_REPO_RE = re.compile(r"^[\w][\w.-]*/[\w][\w.-]*$")


def github_repo_valid(repo: str) -> bool:
    return bool(_GITHUB_REPO_RE.fullmatch(repo))


def aminet_url(path: str) -> str:
    return f"https://aminet.net/{path}"


def github_release_asset_url(repo: str, tag: str, filename: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{urllib.parse.quote(filename)}"


def github_api_url(repo: str, tag: str | None = None) -> str:
    """API url of the tagged release, or of the latest one when tag is None"""
    selector = f"tags/{urllib.parse.quote(tag, safe='')}" if tag else "latest"
    return f"https://api.github.com/repos/{repo}/releases/{selector}"
