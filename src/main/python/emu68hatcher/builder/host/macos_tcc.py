"""full disk access for the app on macOS"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def has_full_disk_access() -> bool:
    """the system tcc folder is listable only with full disk access"""
    try:
        os.listdir("/Library/Application Support/com.apple.TCC")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def open_full_disk_access_pane() -> bool:
    """open system settings -> privacy & security -> full disk access"""
    url = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
    try:
        subprocess.run(["open", url], check=False, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"failed to open FDA settings: {e}")
        return False
