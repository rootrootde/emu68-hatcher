#!/usr/bin/env python3
"""script to simplify emu68hatcher setup

usage (linux/macos - on windows replace 'python3' with 'python'):
    python3 bootstrap.py              # create venv + install, drop into activated shell
    python3 bootstrap.py --dev        # also install dev deps (ruff)
    python3 bootstrap.py --no-shell   # skip the shell drop, just print hint

"""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

MIN_PY = (3, 10)
VENV_DIR = Path(__file__).parent / ".venv"


def activated_env(venv_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv_dir)
    bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.pop("PYTHONHOME", None)
    return env


def pick_shell() -> list[str]:
    if os.name == "nt":
        if os.environ.get("MSYSTEM"):
            bash = shutil.which("bash")
            if bash:
                return [bash]
        return [os.environ.get("ComSpec", "cmd.exe"), "/K"]
    return [os.environ.get("SHELL", "/bin/sh")]


def main() -> int:
    if sys.version_info < MIN_PY:
        have = ".".join(str(n) for n in sys.version_info[:3])
        need = ".".join(str(n) for n in MIN_PY)
        print(f"error: python {need}+ required, got {have}", file=sys.stderr)
        return 1

    target = ".[dev]" if "--dev" in sys.argv[1:] else "."

    if not VENV_DIR.exists():
        print(f"creating venv at {VENV_DIR}...")
        try:
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        except subprocess.CalledProcessError:
            print(
                "\nerror: failed to create venv (ensurepip not available?)\n"
                "on debian/ubuntu install the venv module first:\n"
                "    sudo apt install python3-venv\n"
                "on fedora:\n"
                "    sudo dnf install python3-venv\n"
                "then re-run: python3 bootstrap.py",
                file=sys.stderr,
            )
            shutil.rmtree(VENV_DIR, ignore_errors=True)
            return 1
    else:
        print(f"reusing existing venv at {VENV_DIR}")

    in_msys = bool(os.environ.get("MSYSTEM"))

    if os.name == "nt":
        py = VENV_DIR / "Scripts" / "python.exe"
        if in_msys:
            activate = f"source {VENV_DIR.as_posix()}/Scripts/activate"
        else:
            activate = f"{VENV_DIR}\\Scripts\\activate"
    else:
        py = VENV_DIR / "bin" / "python"
        activate = f"source {VENV_DIR}/bin/activate"

    print(f"installing emu68hatcher ({target}) in editable mode...")
    result = subprocess.run([str(py), "-m", "pip", "install", "-e", target])
    if result.returncode != 0:
        return result.returncode

    repo_root = Path(__file__).parent
    hooks_dir = repo_root / ".githooks"
    if hooks_dir.is_dir() and (repo_root / ".git").exists():
        subprocess.run(
            ["git", "config", "core.hooksPath", ".githooks"],
            cwd=repo_root,
        )
        print("configured git hooksPath -> .githooks")

    is_interactive = sys.stdout.isatty() or in_msys
    if "--no-shell" in sys.argv[1:] or not is_interactive:
        print(f"\ndone. to run:\n  {activate}\n  emu68hatcher")
        return 0

    shell_cmd = pick_shell()
    print(f"\ndone. dropping into activated shell ({shell_cmd[0]}). exit/Ctrl-D to leave.")
    if os.name == "nt":
        try:
            return subprocess.call(shell_cmd, env=activated_env(VENV_DIR))
        except KeyboardInterrupt:
            return 130
    os.execvpe(shell_cmd[0], shell_cmd, activated_env(VENV_DIR))


if __name__ == "__main__":
    sys.exit(main())
