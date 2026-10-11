"""Shared storage draft and AGS inspection/planning, independent of page visibility."""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from emu68hatcher.config.ags_layout import apply_ags_layout, plan_ags_layout
from emu68hatcher.config.ags_models import AGSComponents
from emu68hatcher.gui.extra_content_inspector import ExtraContentInspector
from emu68hatcher.gui.partition_editor_model import PartitionEditorModel


class AGSInspectWorker(QThread):
    inspected = Signal(int, object, str)

    def __init__(self, source_path, roles, generation, *, refresh, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.roles = roles
        self.generation = generation
        self.refresh = refresh

    def run(self):
        try:
            from emu68hatcher.builder.ags_inspection import inspect_ags_source

            inventory = inspect_ags_source(
                self.source_path,
                self.roles,
                cancel_check=self.isInterruptionRequested,
                refresh=self.refresh,
            )
        except Exception as error:
            self.inspected.emit(self.generation, None, str(error) or type(error).__name__)
            return
        self.inspected.emit(self.generation, inventory, "")


class StorageController(QObject):
    changed = Signal()
    layout_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = PartitionEditorModel(64)
        self.output = {}
        self.extras = ExtraContentInspector(self.model, self)
        self.extras.changed.connect(self.changed.emit)
        self.enabled = False
        self._ags_configured = False
        self.source = ""
        self.roles = AGSComponents().selected_roles()
        self.inventory = None
        self.generation = 0
        self.revision = 0
        self.allocation_state = "pending"
        self.inspection_error = ""
        self.layout_message = ""
        self.legacy_content_device = None
        self.migration_notice = None
        self._workers = set()
        self._requested = False
        self._refresh = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._start_inspection)

    def set_output(self, output):
        self.output = dict(output)
        self.changed.emit()

    def cancel_inspection(self):
        self.generation += 1
        self._timer.stop()
        self._requested = False
        for worker in self._workers:
            worker.requestInterruption()

    def _schedule(self):
        if self.enabled and self.source:
            self._requested = True
            self._timer.start()

    def set_selection(self, enabled, source, roles):
        source = source.strip()
        source_changed = source != self.source
        selection_changed = (enabled, roles) != (self.enabled, self.roles)
        if not source_changed and not selection_changed:
            return
        self.cancel_inspection()
        self._ags_configured = True
        self.enabled, self.source, self.roles = enabled, source, roles
        if source_changed:
            self.inventory = None
            self.inspection_error = ""
            self._refresh = False
        available = {item.role for item in self.inventory.components} if self.inventory else set()
        if enabled and not set(roles).issubset(available):
            self._schedule()
        self.replan()

    def refresh_source(self):
        self.cancel_inspection()
        self.inventory = None
        self.inspection_error = ""
        self._refresh = True
        self._schedule()
        self.replan()

    def _start_inspection(self):
        if not self._requested or self._workers:
            return
        self._requested = False
        if not self.enabled or not self.source:
            return
        worker = AGSInspectWorker(
            Path(self.source),
            self.roles,
            self.generation,
            refresh=self._refresh,
            parent=self,
        )
        self._refresh = False
        self._workers.add(worker)
        worker.inspected.connect(self.accept_inspection)
        worker.finished.connect(self._worker_finished)
        worker.start()
        self.changed.emit()

    @Slot(int, object, str)
    def accept_inspection(self, generation, inventory, error):
        if generation != self.generation:
            return
        self.inventory = inventory if not error else None
        self.inspection_error = error
        self.replan()

    @Slot()
    def _worker_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        worker.deleteLater()
        if self._requested:
            self._timer.start()
        self.changed.emit()

    def inspection_pending(self):
        return bool(self._workers) or self._requested or self._timer.isActive()

    def set_partitions(self, config, *, update=True):
        self.model.load(config)
        if update:
            self.replan()

    def replan(self):
        if not self._ags_configured:
            self.layout_changed.emit()
            self.changed.emit()
            return
        self.revision += 1
        roles = self.roles if self.enabled else ()
        requirements = (
            {item.role: item.partition.size for item in self.inventory.components}
            if self.inventory
            else {}
        )
        self.allocation_state = "pending"
        if self.enabled and (self.inventory is None or not set(roles).issubset(requirements)):
            self.layout_message = (
                "Partition sizes will update when the source check finishes."
                if self.inspection_pending()
                else "Select a readable AGS image to continue."
            )
            self.changed.emit()
            return
        draft = self.model.to_layout_draft()
        identity = self.inventory.identity if self.enabled else None
        proposal = plan_ags_layout(
            draft,
            requirements,
            roles,
            source_identity=identity,
            selection_revision=self.revision,
        )
        if proposal.errors:
            draft.layout[1].amiga_partitions = list(proposal.partitions)
        else:
            draft = apply_ags_layout(
                draft,
                proposal,
                source_identity=identity,
                selection_revision=self.revision,
            )
            self.allocation_state = "ready"
            self.legacy_content_device = self.migration_notice = None
        if draft.model_dump() != self.model.to_layout_draft().model_dump():
            self.model.load(draft)
        selected = sum(requirements[role] for role in roles)
        lines = [f"{selected / 1024**3:.2f} GiB selected"] if self.enabled else []
        if proposal.free_bytes < 0:
            lines.append(
                f"Capacity shortfall: {-proposal.free_bytes / 1024**3:.2f} GiB. "
                "Deselect content, choose a larger target, or adjust manual partitions."
            )
        lines.extend(error for error in proposal.errors if "exceeds RDB capacity" not in error)
        if self.migration_notice:
            lines.append(self.migration_notice)
        self.layout_message = "\n".join(lines)
        self.layout_changed.emit()
        self.changed.emit()

    def load_ags(self, config):
        self.cancel_inspection()
        self._ags_configured = True
        self.enabled = bool(config and config.enabled)
        self.source = str(config.source_image) if config else ""
        self.roles = (config.components if config else AGSComponents()).selected_roles()
        self.inventory = None
        self.inspection_error = ""
        self._refresh = False
        self.legacy_content_device = config.legacy_content_device if config else None
        self.migration_notice = config.migration_notice if config else None
        self._schedule()
        self.replan()

    def ags_config(self):
        reservations = any(part.ags_reservation for part in self.model.partitions)
        if not self.source and not self.enabled and not reservations:
            return None
        if not self.source:
            raise ValueError("Select an AGS source image")
        return {
            "source_image": self.source,
            "enabled": self.enabled,
            "components": {
                role: role in self.roles for role in ("whdload", "games", "work", "media")
            },
            "allocation_state": self.allocation_state,
            "legacy_content_device": self.legacy_content_device,
            "migration_notice": self.migration_notice,
        }

    def shutdown_workers(self, timeout_ms=500):
        self.cancel_inspection()
        workers = tuple(worker for worker in self._workers if worker.isRunning())
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)
