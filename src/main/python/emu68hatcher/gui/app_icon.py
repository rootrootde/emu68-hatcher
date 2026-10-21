"""Application icon lookup and rendering."""

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


def render_icon(path: Path, size: int) -> QPixmap:
    """render an svg/png/icns to a square QPixmap of the given size"""
    screen = QApplication.primaryScreen()
    pixel_ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixel_size = max(size, round(size * pixel_ratio))
    if path.suffix.lower() == ".svg":
        renderer = QSvgRenderer(str(path))
        pixmap = QPixmap(pixel_size, pixel_size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(pixel_ratio)
        return pixmap
    return QIcon(str(path)).pixmap(QSize(size, size), pixel_ratio)


def find_app_icon() -> Path | None:
    """locate the app icon - frozen: fbs-emitted Icon.{ico,icns} next to binary; dev: hatcher-icon.svg in src tree"""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        for candidate in (
            exe_dir / "Icon.ico",  # windows + linux
            exe_dir.parent / "Resources" / "Icon.icns",  # macos .app
            exe_dir / "Icon.icns",
        ):
            if candidate.is_file():
                return candidate
        return None
    here = Path(__file__).resolve()
    for parent in here.parents:
        svg = parent / "src" / "main" / "icons" / "hatcher-icon.svg"
        if svg.is_file():
            return svg
    return None
