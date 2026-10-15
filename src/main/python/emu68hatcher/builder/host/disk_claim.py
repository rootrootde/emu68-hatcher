"""macos DA claim - stops diskarbitrationd probing the target during a build"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import (
    c_char_p,
    c_double,
    c_int32,
    c_uint8,
    c_uint64,
    c_void_p,
)

logger = logging.getLogger(__name__)
_failed_claims: list[DiskClaim] = []


class DiskClaim:
    """DA claim on /dev/diskN for the build; macOS only"""

    def __init__(self, device: str) -> None:
        self.device = device
        self._claimed = False
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def acquire(self) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            logger.warning("disk claim did not signal ready in 3s")
            self._stop.set()
            self._thread.join(timeout=5.0)
            return False
        if self._error:
            logger.warning(f"disk claim failed: {self._error}")
            return False
        return self._claimed

    def release(self) -> bool:
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.error("disk claim thread did not stop within 5s")
                return False
            self._thread = None
        self._claimed = False
        return True

    def _run(self) -> None:
        try:
            cf = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
            da = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/DiskArbitration.framework/DiskArbitration"
            )

            cf.CFRunLoopGetCurrent.restype = c_void_p
            cf.CFRunLoopRunInMode.restype = c_int32
            cf.CFRunLoopRunInMode.argtypes = [c_void_p, c_double, c_uint8]
            cf.CFRelease.argtypes = [c_void_p]
            default_mode = c_void_p.in_dll(cf, "kCFRunLoopDefaultMode").value

            da.DASessionCreate.restype = c_void_p
            da.DASessionCreate.argtypes = [c_void_p]
            da.DASessionScheduleWithRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]
            da.DASessionUnscheduleFromRunLoop.argtypes = [c_void_p, c_void_p, c_void_p]
            da.DADiskCreateFromBSDName.restype = c_void_p
            da.DADiskCreateFromBSDName.argtypes = [c_void_p, c_void_p, c_char_p]
            da.DADiskClaim.argtypes = [
                c_void_p,
                c_uint64,
                c_void_p,
                c_void_p,
                c_void_p,
                c_void_p,
            ]
            da.DADiskUnclaim.argtypes = [c_void_p]

            session = da.DASessionCreate(None)
            if not session:
                self._error = "DASessionCreate returned NULL"
                self._ready.set()
                return

            runloop = cf.CFRunLoopGetCurrent()
            da.DASessionScheduleWithRunLoop(session, runloop, default_mode)

            disk = da.DADiskCreateFromBSDName(None, session, self.device.encode())
            if not disk:
                self._error = f"DADiskCreateFromBSDName({self.device}) returned NULL"
                da.DASessionUnscheduleFromRunLoop(session, runloop, default_mode)
                cf.CFRelease(session)
                self._ready.set()
                return

            # NULL release-cb denies competing claims
            da.DADiskClaim(disk, 0, None, None, None, None)

            # pump briefly so the claim XPC round-trip completes
            cf.CFRunLoopRunInMode(default_mode, 0.5, 0)
            self._claimed = True
            self._ready.set()

            # runloop stays alive so DA can deliver "competing claim" events
            while not self._stop.is_set():
                cf.CFRunLoopRunInMode(default_mode, 0.25, 0)

            da.DADiskUnclaim(disk)
            cf.CFRelease(disk)
            da.DASessionUnscheduleFromRunLoop(session, runloop, default_mode)
            cf.CFRelease(session)
        except (OSError, ValueError, AttributeError) as e:
            self._error = str(e)
            self._ready.set()


def claim_macos_disk(device: str) -> DiskClaim | None:
    """build + acquire a DiskClaim; None if it cannot be obtained"""
    claim = DiskClaim(device)
    if claim.acquire():
        return claim
    if not claim.release():
        _failed_claims.append(claim)
        logger.error("retaining failed disk claim until its worker exits")
    return None
