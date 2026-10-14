"""render the documentation screenshots from the offscreen gui"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "assets" / "screenshots"

# (file name, work area, section): sections are the tabs of the System area
PAGES = [
    ("overview", "overview", ""),
    ("system-hardware", "system", "hardware"),
    ("system-amiga", "system", "amiga"),
    ("system-network", "system", "network"),
    ("appearance", "appearance", ""),
    ("software", "software", ""),
    ("storage", "storage", ""),
    ("review", "review", ""),
]
WIDTH = 1200
MIN_HEIGHT = 800
MAX_HEIGHT = 1600


def _wait(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _wait_for_update_check(app, window, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        worker = window.start_tab._update_worker
        if worker is None or worker.isFinished():
            return
        time.sleep(0.05)


def _fit_height(app, window):
    """grow the window until the visible scroll areas show without a scroll bar"""
    from PySide6.QtWidgets import QScrollArea

    window.resize(WIDTH, MIN_HEIGHT)
    _wait(app, 0.2)
    current = window.stack.currentWidget()
    extra = 0
    for scroll in [current, *current.findChildren(QScrollArea)]:
        if not isinstance(scroll, QScrollArea) or not scroll.isVisible():
            continue
        content = scroll.widget()
        if content is not None:
            extra = max(extra, content.sizeHint().height() - scroll.viewport().height())
    if extra > 0:
        window.resize(WIDTH, min(MAX_HEIGHT, MIN_HEIGHT + extra + 8))
        _wait(app, 0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--scale", type=float, default=2.0, help="device pixel ratio")
    parser.add_argument(
        "--check-updates", action="store_true", help="run the online update check first"
    )
    parser.add_argument("--only", nargs="*", default=None, help="file names to render")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    sys.path.insert(0, str(ROOT / "src" / "main" / "python"))

    from emu68hatcher.gui.tabs.start import StartTab
    from PySide6.QtWidgets import QApplication

    if not args.check_updates:
        StartTab.check_for_updates = lambda self: None

    app = QApplication.instance() or QApplication([])
    from emu68hatcher.gui.main_window import MainWindow

    window = MainWindow()
    window.resize(WIDTH, MIN_HEIGHT)
    window.show()
    _wait(app, 0.5)
    if args.check_updates:
        _wait_for_update_check(app, window, timeout=20)
        _wait(app, 0.3)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, page, section in PAGES:
        if args.only and name not in args.only:
            continue
        window.navigate(page, section)
        _fit_height(app, window)
        _wait(app, 0.3)
        target = args.out / f"{name}.png"
        window.grab().save(str(target))
        print(f"wrote {target}")

    window.close()
    app.processEvents()


if __name__ == "__main__":
    main()
