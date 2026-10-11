"""Revision-bound extra-file size checks owned by the storage draft."""

import os
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, Signal, Slot

from emu68hatcher.config.partition_helpers import usable_partition_content_size
from emu68hatcher.gui.workers import ExtraContentSizeWorker


class ExtraContentInspector(QObject):
    changed = Signal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.workers = set()
        self.generation = 0
        self.pending = set()
        self.usage = {}
        self.scan_errors = {}

    @staticmethod
    def key(path):
        return os.path.normcase(os.path.abspath(path.expanduser()))

    def directories(self):
        return {
            self.key(Path(part.extra_content_directory)): Path(
                self.key(Path(part.extra_content_directory))
            )
            for part in self.model.partitions
            if part.extra_content_directory is not None
        }

    def scan(self):
        self.generation += 1
        generation = self.generation
        for worker in self.workers:
            worker.requestInterruption()
        directories = self.directories()
        self.pending = set(directories)
        self.usage.clear()
        self.scan_errors.clear()
        if directories:
            worker = ExtraContentSizeWorker(list(directories.values()), self)
            worker.generation = generation
            self.workers.add(worker)
            worker.size_found.connect(self._accept_usage)
            worker.scan_error.connect(self._accept_error)
            worker.finished.connect(self._finished)
            worker.start()
        self.changed.emit()

    @Slot(str, object)
    def _accept_usage(self, path, usage):
        if self.sender().generation != self.generation:
            return
        key = self.key(Path(path))
        self.usage[key] = usage
        self.scan_errors.pop(key, None)
        self.pending.discard(key)
        self.changed.emit()

    @Slot(str, str)
    def _accept_error(self, path, error):
        if self.sender().generation != self.generation:
            return
        key = self.key(Path(path))
        self.scan_errors[key] = error
        self.pending.discard(key)
        self.changed.emit()

    @Slot()
    def _finished(self):
        worker = self.sender()
        self.workers.discard(worker)
        worker.deleteLater()
        if worker.generation == self.generation:
            for path in worker.directories:
                key = self.key(path)
                self.pending.discard(key)
                if key not in self.usage and key not in self.scan_errors:
                    self.scan_errors[key] = (
                        "Folder check did not finish. Select the folder again to retry."
                    )
            self.changed.emit()

    @staticmethod
    def format_size(size):
        if size >= 1024**3:
            return f"{size / 1024**3:.1f} GiB"
        if size >= 1024**2:
            return f"{size / 1024**2:.1f} MiB"
        return f"{size / 1024:.1f} KiB"

    def status(self, part):
        if part.extra_content_directory is None:
            return "", None
        path = Path(part.extra_content_directory).expanduser()
        key = self.key(path)
        if key in self.pending:
            return "checking…", None
        if not path.is_dir():
            return "folder not found", "error"
        if key in self.scan_errors:
            return "could not read folder", "error"
        usage = self.usage.get(key)
        if usage is None:
            return "not checked", "error"
        required = usage.estimated_bytes
        usable = usable_partition_content_size(part.size)
        text = f"{self.format_size(required)} / {self.format_size(usable)}"
        return (f"{text} – too large", "error") if required > usable else (f"{text} – fits", "ok")

    def scan_pending(self):
        return bool(self.pending.intersection(self.directories()))

    def errors(self):
        errors = []
        for part in self.model.partitions:
            if part.extra_content_directory is None:
                continue
            text, state = self.status(part)
            if state == "error":
                errors.append(f"{part.device} ({part.volume}): {text}")
        return errors

    def shutdown_workers(self, timeout_ms=500):
        self.generation += 1
        for key in self.pending:
            self.scan_errors[key] = "Folder check was cancelled. Select the folder again to retry."
        self.pending.clear()
        self.changed.emit()
        workers = tuple(worker for worker in self.workers if worker.isRunning())
        for worker in workers:
            worker.requestInterruption()
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)
