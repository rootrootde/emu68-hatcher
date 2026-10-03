"""Console logging and shared file handler helper."""

import logging
from pathlib import Path

_logger = logging.getLogger("emu68hatcher")
_logger.setLevel(logging.INFO)
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


def get_logger() -> logging.Logger:
    return _logger
