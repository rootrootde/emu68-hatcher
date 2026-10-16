"""Emu68 Hatcher entry point"""

import os
import sys


def _augment_path_for_gui_launch():
    """.app has only minimal PATH -> add paths so 7z etc resolve"""
    if sys.platform != "darwin":
        return
    extras = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        "/opt/local/bin",
        "/opt/local/sbin",
    ]
    current = os.environ.get("PATH", "").split(os.pathsep)
    for p in extras:
        if p not in current:
            current.insert(0, p)
    os.environ["PATH"] = os.pathsep.join(current)


def _point_ssl_at_certifi():
    """frozen python has no system CA -> use certifi for urllib/requests"""
    if not getattr(sys, "frozen", False):
        return
    try:
        import certifi
    except ImportError:
        return
    bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)


def _hide_windows_subprocess_consoles():
    """prevent flashing console window"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import subprocess

    _orig = subprocess.Popen.__init__

    def _patched(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        _orig(self, *args, **kwargs)

    subprocess.Popen.__init__ = _patched


def _restore_subprocess_ld_library_path():
    """undo pyinstaller's bundle LD_LIBRARY_PATH so subprocs use system libs"""
    if sys.platform != "linux" or not getattr(sys, "frozen", False):
        return
    orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        os.environ["LD_LIBRARY_PATH"] = orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def _is_elevated_worker_launch() -> bool:
    """frozen exe re-launched by the elevated helper as `<exe> emu68hatcher-worker-*.py <ipc_dir>`"""
    return (
        getattr(sys, "frozen", False)
        and len(sys.argv) >= 3
        and sys.argv[1].lower().endswith(".py")
        and "emu68hatcher-worker" in os.path.basename(sys.argv[1]).lower()
    )


def main():
    # frozen builds have no separate python, so the elevated helper re-launches THIS exe as the
    # worker's interpreter. run the worker headless instead of booting a second GUI window (which
    # never signals ready -> 30s timeout -> broken per-call fallback).
    if _is_elevated_worker_launch():
        import runpy

        _hide_windows_subprocess_consoles()  # the worker spawns icacls + hst-imager
        _restore_subprocess_ld_library_path()  # pkexec starts a fresh bundle env on linux
        sys.argv = sys.argv[1:]  # worker reads ipc_dir from sys.argv[1]
        runpy.run_path(sys.argv[0], run_name="__main__")
        return

    _augment_path_for_gui_launch()
    _point_ssl_at_certifi()
    _hide_windows_subprocess_consoles()
    _restore_subprocess_ld_library_path()

    from emu68hatcher.app import run

    run()


if __name__ == "__main__":
    main()
