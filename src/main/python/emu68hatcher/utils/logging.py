"""Console logging and shared file handler helper."""

import faulthandler
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_logger = logging.getLogger("emu68hatcher")
_logger.setLevel(logging.DEBUG)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
_logger.addHandler(_console_handler)


def attach_file_handler(
    logger: logging.Logger,
    path: Path,
    mode: str = "a",
    level: int = logging.DEBUG,
    fmt: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt: str | None = None,
) -> logging.FileHandler | None:
    """attach a FileHandler to logger; return None if open failed (failure logged via logger)"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, mode=mode, encoding="utf-8")
    except Exception as e:
        # broad except: TCC/sandbox failures on macOS Tahoe don't always raise OSError
        logger.warning(
            f"could not open log file at {path}: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt, datefmt))
    logger.addHandler(handler)
    return handler


def start_app_log(path: Path) -> None:
    """log this run to path, keep the previous run as path.1, native crashes land there too"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, backupCount=1, encoding="utf-8")
        if path.stat().st_size:
            handler.doRollover()
    except Exception as e:
        _logger.warning(f"could not open log file at {path}: {type(e).__name__}: {e}")
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    _logger.addHandler(handler)
    # a segfault or abort skips every python handler; faulthandler writes straight to the fd
    faulthandler.enable(file=handler.stream)


def get_logger() -> logging.Logger:
    return _logger
