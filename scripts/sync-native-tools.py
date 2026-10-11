#!/usr/bin/env python3
"""Copy the native tool release archives from their sibling repos into local_packages.

For each tool the archive is copied to data/local_packages/<name>.lha, the MD5 in
the package YAML's download.hash is updated, and data/local_packages/native-tools.json
records the source commit, size, SHA-256 and MD5.

By default the repo's existing archive is copied, only when its tracked files are
clean and the archive is newer than HEAD, so the recorded commit describes it.
--build instead exports HEAD with `git archive` into a temp dir,
seeds the already downloaded dependencies and runs the repo's documented Docker
package target there (no network), which never touches the sibling checkout.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/main/python/emu68hatcher/data"
LOCAL = DATA / "local_packages"
LOCK = LOCAL / "native-tools.json"
IMAGE = (
    "amigadev/crosstools@sha256:6b8e43ad3315034778dfa3266bbef2f2c470927c3e6b66796b6d56a9e5680c75"
)


@dataclass(frozen=True)
class Tool:
    repo: str
    archive: str  # relative to the repo root
    build_dir: str  # relative to the repo root
    seed: tuple[str, ...]  # dependency paths under build_dir copied into a clean export
    command: tuple[str, ...]  # documented package target
    docker_workdir: str | None  # None: command runs on the host (its Makefile calls docker)
    package: str  # package YAML name
    dest: str  # file name under local_packages


TOOLS = {
    "hatcher-prefs": Tool(
        repo="hatcher-prefs",
        archive=".build/Hatcher-Prefs-dev.lha",
        build_dir=".build",
        seed=("deps/mui38dev.lha",),
        command=("make", "dist"),
        docker_workdir=None,
        package="hatcher_prefs",
        dest="Hatcher-Prefs.lha",
    ),
    "hatcher-packages": Tool(
        repo="hatcher-packages",
        archive="amiga/.build/Hatcher-Packages-dev.lha",
        build_dir="amiga/.build",
        seed=("deps", "mui38-include"),
        command=("make", "all", "package"),
        docker_workdir="amiga",
        package="hatcher_packages",
        dest="Hatcher-Packages.lha",
    ),
    "emu68-manager": Tool(
        repo="emu68-manager",
        archive="amiga/.build/Emu68-Manager.lha",
        build_dir="amiga/.build",
        seed=("deps", "mui38-include"),
        command=("make", "package-release"),
        docker_workdir="amiga",
        package="emu68_manager",
        dest="Emu68-Manager.lha",
    ),
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _digest(path: Path, name: str) -> str:
    digest = hashlib.new(name)
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)


def _clean_build(tool: Tool, repo: Path, commit: str, work: Path) -> Path:
    export = work / tool.repo
    export.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit],
        check=True,
        capture_output=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive, check=True)
    for rel in tool.seed:
        src = repo / tool.build_dir / rel
        if not src.exists():
            raise SystemExit(f"{tool.repo}: missing dependency {src}; run its deps target first")
        _copy_tree(src, export / tool.build_dir / rel)
    if tool.docker_workdir is None:
        cmd = list(tool.command)
        cwd = export
    else:
        cmd = [
            "docker", "run", "--rm", "--pull", "never", "--network", "none",
            "-v", f"{export}:/work", "-w", f"/work/{tool.docker_workdir}", IMAGE,
            *tool.command,
        ]  # fmt: skip
        cwd = None
    print(f"{tool.repo}: building {commit[:12]} in {export}", flush=True)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"{tool.repo}: build failed ({result.returncode})")
    built = export / tool.archive
    if not built.is_file():
        raise SystemExit(f"{tool.repo}: build produced no {tool.archive}")
    return built


def _update_package_hash(tool: Tool, md5: str) -> None:
    path = DATA / "packages" / f"{tool.package}.yaml"
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(r"(?m)^(  hash:) ?[0-9A-Fa-f]*$", rf"\g<1> {md5.upper()}", text)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one download hash line")
    path.write_text(new, encoding="utf-8", newline="\n")


def sync(name: str, siblings: Path, build: bool, lock: dict) -> None:
    tool = TOOLS[name]
    repo = siblings / tool.repo
    if not (repo / ".git").exists():
        raise SystemExit(f"{name}: no git checkout at {repo}")
    commit = _git(repo, "rev-parse", "HEAD")
    # a prebuilt archive may contain uncommitted work; --build exports the commit itself
    if not build and _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"{name}: {repo} has uncommitted changes; commit them or pass --build")

    with tempfile.TemporaryDirectory(prefix="native-tools-") as tmp:
        if build:
            source = _clean_build(tool, repo, commit, Path(tmp))
        else:
            source = repo / tool.archive
            if not source.is_file():
                raise SystemExit(f"{name}: {source} missing; build it or pass --build")
            committed = int(_git(repo, "log", "-1", "--format=%ct"))
            if source.stat().st_mtime < committed:
                raise SystemExit(f"{name}: {source} is older than {commit[:12]}; pass --build")
        dest = LOCAL / tool.dest
        shutil.copyfile(source, dest)

    md5 = _digest(dest, "md5")
    lock[name] = {
        "repository": tool.repo,
        "commit": commit,
        "archive": tool.archive,
        "built_clean": build,
        "file": tool.dest,
        "size": dest.stat().st_size,
        "sha256": _digest(dest, "sha256"),
        "md5": md5,
    }
    _update_package_hash(tool, md5)
    print(f"{name}: {tool.dest} from {commit[:12]} sha256 {lock[name]['sha256']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tools", nargs="*", help=f"any of {', '.join(TOOLS)} (default: all)")
    parser.add_argument("--siblings", type=Path, default=ROOT.parent)
    parser.add_argument("--build", action="store_true", help="rebuild from a clean export")
    args = parser.parse_args()
    unknown = set(args.tools) - set(TOOLS)
    if unknown:
        parser.error(f"unknown tool: {', '.join(sorted(unknown))}")

    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    for name in args.tools or TOOLS:
        sync(name, args.siblings.resolve(), args.build, lock)
        # written per tool so a later failure keeps the record of what was copied
        LOCK.write_text(
            json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
