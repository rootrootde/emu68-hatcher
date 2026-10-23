#!/usr/bin/env python3
"""Copy the Hatcher-Prefs archive from the sibling hatcher-prefs repo into local_packages."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/main/python/emu68hatcher/data"
LOCAL = DATA / "local_packages"
LOCK = LOCAL / "native-tools.json"

NAME = "hatcher-prefs"  # key in native-tools.json
REPO = "hatcher-prefs"
SUBMODULES = ("lib",)  # git archive skips gitlinks, so these are exported separately
ARCHIVE = ".build/Hatcher-Prefs.lha"  # relative to the repo root
BUILD_DIR = ".build"  # relative to the repo root
SEED = ("deps",)  # dependency paths under BUILD_DIR copied into a clean export
COMMAND = ("make", "dist")  # its Makefile calls docker
DEST = "Hatcher-Prefs.lha"  # file name under local_packages


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


def _clean_build(repo: Path, commit: str, work: Path) -> Path:
    export = work / REPO
    export.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit],
        check=True,
        capture_output=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive, check=True)
    for rel in SUBMODULES:
        sub = repo / rel
        if not (sub / ".git").exists():
            raise SystemExit(
                f"{REPO}: submodule {rel} not checked out; run git submodule update --init"
            )
        pinned = _git(repo, "rev-parse", f"{commit}:{rel}")
        tree = subprocess.run(
            ["git", "-C", str(sub), "archive", "--format=tar", pinned],
            check=True,
            capture_output=True,
        ).stdout
        (export / rel).mkdir(exist_ok=True)
        subprocess.run(["tar", "-x", "-C", str(export / rel)], input=tree, check=True)
    for rel in SEED:
        src = repo / BUILD_DIR / rel
        if not src.exists():
            raise SystemExit(f"{REPO}: missing dependency {src}; run its deps target first")
        _copy_tree(src, export / BUILD_DIR / rel)
    print(f"{REPO}: building {commit[:12]} in {export}", flush=True)
    result = subprocess.run(list(COMMAND), cwd=export, capture_output=True, text=True)
    if result.returncode:
        sys.stderr.write(result.stdout[-4000:] + result.stderr[-4000:])
        raise SystemExit(f"{REPO}: build failed ({result.returncode})")
    built = export / ARCHIVE
    if not built.is_file():
        raise SystemExit(f"{REPO}: build produced no {ARCHIVE}")
    return built


def sync(siblings: Path, build: bool, lock: dict) -> None:
    repo = siblings / REPO
    if not (repo / ".git").exists():
        raise SystemExit(f"{NAME}: no git checkout at {repo}")
    commit = _git(repo, "rev-parse", "HEAD")
    # a prebuilt archive may contain uncommitted work; --build exports the commit itself
    if not build and _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"{NAME}: {repo} has uncommitted changes; commit them or pass --build")

    with tempfile.TemporaryDirectory(prefix="native-tools-") as tmp:
        if build:
            source = _clean_build(repo, commit, Path(tmp))
        else:
            source = repo / ARCHIVE
            if not source.is_file():
                raise SystemExit(f"{NAME}: {source} missing; build it or pass --build")
            committed = int(_git(repo, "log", "-1", "--format=%ct"))
            if source.stat().st_mtime < committed:
                raise SystemExit(f"{NAME}: {source} is older than {commit[:12]}; pass --build")
        dest = LOCAL / DEST
        shutil.copyfile(source, dest)

    lock[NAME] = {
        "repository": REPO,
        "commit": commit,
        "archive": ARCHIVE,
        "built_clean": build,
        "file": DEST,
        "size": dest.stat().st_size,
        "sha256": _digest(dest, "sha256"),
        "md5": _digest(dest, "md5"),
    }
    print(f"{NAME}: {DEST} from {commit[:12]} sha256 {lock[NAME]['sha256']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--siblings", type=Path, default=ROOT.parent)
    parser.add_argument("--build", action="store_true", help="rebuild from a clean export")
    args = parser.parse_args()

    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    sync(args.siblings.resolve(), args.build, lock)
    LOCK.write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
