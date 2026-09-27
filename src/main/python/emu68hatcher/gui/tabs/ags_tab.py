"""AGS source selection and partition proposals."""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.ags_layout import apply_ags_layout, plan_ags_layout
from emu68hatcher.config.ags_models import AGS_ROLES, AGSComponents, AGSRole
from emu68hatcher.config.partition_models import PartitionConfig
from emu68hatcher.config.schema import AGSImportConfig


class AGSInspectWorker(QThread):
    inspected = Signal(int, object, str)

    def __init__(
        self,
        source_path: Path,
        roles: tuple[AGSRole, ...],
        generation: int,
        *,
        refresh: bool,
        parent=None,
    ):
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


class AGSProposalDialog(QDialog):
    def __init__(self, layout, requirements, roles, source_identity, revision, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review AGS partitions")
        self.layout_config = layout
        self.requirements = requirements
        self.roles = roles
        self.source_identity = source_identity
        self.revision = revision
        self.requested_edits: dict[str, int] = {}
        self.proposal = None
        outer = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        form = QFormLayout()
        self.shrink_combo = QComboBox()
        for part in layout.iter_amiga_partitions():
            if not part.ags_reservation:
                self.shrink_combo.addItem(f"{part.device}: {part.volume}", part.device)
        form.addRow("Release space from:", self.shrink_combo)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 1048576)
        self.size_spin.setSuffix(" MiB")
        form.addRow("New size:", self.size_spin)
        self.shrink_btn = QPushButton("Use this size")
        self.shrink_btn.clicked.connect(self._set_manual_size)
        form.addRow("", self.shrink_btn)
        outer.addLayout(form)
        self.shrink_combo.currentIndexChanged.connect(self._sync_size)
        self._sync_size()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply partition changes")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._recalculate()

    def _sync_size(self):
        device = self.shrink_combo.currentData()
        part = next(
            (part for part in self.layout_config.iter_amiga_partitions() if part.device == device),
            None,
        )
        enabled = part is not None
        self.size_spin.setEnabled(enabled)
        self.shrink_btn.setEnabled(enabled)
        if part:
            self.size_spin.setValue(self.requested_edits.get(device, part.size) // 1024**2)

    def _set_manual_size(self):
        device = self.shrink_combo.currentData()
        if device:
            from emu68hatcher.config.constants import CYLINDER_SIZE

            requested = self.size_spin.value() * 1024**2
            self.requested_edits[device] = (requested // CYLINDER_SIZE) * CYLINDER_SIZE
            self._recalculate()

    def _recalculate(self):
        self.proposal = plan_ags_layout(
            self.layout_config,
            self.requirements,
            self.roles,
            self.requested_edits,
            source_identity=self.source_identity,
            selection_revision=self.revision,
        )
        proposal = self.proposal
        lines = [f"Total disk: {self.layout_config.disk_size / 1024**3:.2f} GiB"]
        previous = {part.device: part for part in self.layout_config.iter_amiga_partitions()}
        for part in proposal.partitions:
            old = previous.get(part.device)
            before = f"{old.size / 1024**3:.2f} -> " if old and old.size != part.size else ""
            detail = f"{part.device}: {part.volume} {before}{part.size / 1024**3:.2f} GiB"
            if part.ags_reservation:
                reservation = part.ags_reservation
                detail += f" (AGS partition copy: {part.volume}; fixed {reservation.minimum_size / 1024**3:.2f} GiB)"
            lines.append(detail)
        for role in proposal.released_roles:
            lines.append(f"Release AGS {role} reservation")
        lines.append(f"Unallocated: {proposal.free_bytes / 1024**3:.2f} GiB")
        lines.extend(proposal.errors)
        self.summary.setText("\n".join(lines))
        self.apply_button.setEnabled(not proposal.errors)


class AGSTab(QWidget):
    layout_applied = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: set[AGSInspectWorker] = set()
        self._generation = 0
        self._revision = 0
        self._inventory = None
        self._partitions: PartitionConfig | None = None
        self._allocation_state = "pending"
        self._legacy_content_device = None
        self._migration_notice = None
        self._configured = False
        self._loading = False
        self._committed_enabled = False
        layout = QVBoxLayout(self)
        self.enabled_check = QCheckBox("Import AGS")
        self.enabled_check.toggled.connect(self._enabled_changed)
        layout.addWidget(self.enabled_check)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source image:"))
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("AGS v30 or supported v31 image")
        self.source_edit.textChanged.connect(self._source_changed)
        source_row.addWidget(self.source_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        source_row.addWidget(browse)
        self.inspect_btn = QPushButton("Inspect source")
        self.inspect_btn.clicked.connect(self.inspect_source)
        source_row.addWidget(self.inspect_btn)
        layout.addLayout(source_row)
        self.component_checks = {}
        for role, label, checked in (
            ("whdload", "WHDLoad + AGS", True),
            ("games", "Games and Premium", True),
            ("work", "Work (emulators and applications)", True),
            ("media", "Media", False),
        ):
            check = QCheckBox(label)
            check.setChecked(checked)
            check.setEnabled(role != "whdload")
            check.toggled.connect(self._selection_changed)
            self.component_checks[role] = check
            layout.addWidget(check)
        self.result_label = QLabel("Inspect the source to calculate partition sizes.")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.layout_label = QLabel("AGS partition allocation is pending.")
        self.layout_label.setWordWrap(True)
        layout.addWidget(self.layout_label)
        self.preview_btn = QPushButton("Preview AGS partitions...")
        self.preview_btn.clicked.connect(self.preview_partitions)
        layout.addWidget(self.preview_btn)
        layout.addStretch()
        self._update_buttons()

    def _selected_roles(self) -> tuple[AGSRole, ...]:
        return tuple(role for role in AGS_ROLES if self.component_checks[role].isChecked())

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select AGS image", "", "Disk images (*.img *.hdf);;All Files (*)"
        )
        if path:
            self.source_edit.setText(path)

    def _mark_pending(self):
        self._revision += 1
        self._allocation_state = "pending"
        self._update_layout_label()
        self._update_buttons()

    def _source_changed(self):
        if self._loading:
            return
        self._generation += 1
        self._inventory = None
        for worker in self._workers:
            worker.requestInterruption()
        self._configured = bool(self.source_edit.text().strip()) or self._configured
        self.result_label.setText("Inspect the source to calculate partition sizes.")
        self._mark_pending()

    def _selection_changed(self):
        if self._loading:
            return
        self._generation += 1
        if self._inventory is not None:
            available = {component.role for component in self._inventory.components}
            if not set(self._selected_roles()).issubset(available):
                self._inventory = None
        for worker in self._workers:
            worker.requestInterruption()
        if self._inventory is None:
            self.result_label.setText(
                "Inspect the selected components to calculate partition sizes."
            )
        self._mark_pending()

    def _enabled_changed(self, enabled: bool):
        if self._loading:
            return
        if (
            not enabled
            and self._committed_enabled
            and (self._partitions is None or self._has_reservations())
        ):
            if not self._show_proposal(()):
                self.enabled_check.blockSignals(True)
                self.enabled_check.setChecked(True)
                self.enabled_check.blockSignals(False)
                return
        self._committed_enabled = enabled
        if enabled:
            self._configured = True
            self._mark_pending()
        else:
            self._update_layout_label()
        self._update_buttons()

    def _has_reservations(self) -> bool:
        return self._partitions is not None and any(
            part.ags_reservation for part in self._partitions.iter_amiga_partitions()
        )

    def _update_layout_label(self):
        reserved = (
            []
            if self._partitions is None
            else [
                f"{part.ags_reservation.role}: {part.device} ({part.volume})"
                for part in self._partitions.iter_amiga_partitions()
                if part.ags_reservation
            ]
        )
        state = (
            "ready"
            if self._allocation_state == "ready"
            else "pending; apply a partition proposal before building"
        )
        text = f"AGS allocation: {state}."
        if reserved:
            text += " Reserved: " + ", ".join(reserved) + "."
        if self._legacy_content_device:
            text += (
                f" Old shared target {self._legacy_content_device} needs a new dedicated partition."
            )
        if self._migration_notice:
            text += " " + self._migration_notice
        self.layout_label.setText(text)

    def _update_buttons(self):
        self.inspect_btn.setEnabled(
            self.enabled_check.isChecked()
            and bool(self.source_edit.text().strip())
            and not self._workers
        )
        self.preview_btn.setEnabled(
            self._partitions is not None
            and self.enabled_check.isChecked()
            and self._inventory is not None
            and not self._workers
        )

    def inspect_source(self):
        if (
            self._workers
            or not self.enabled_check.isChecked()
            or not self.source_edit.text().strip()
        ):
            return
        refresh = self._inventory is not None
        self._generation += 1
        generation = self._generation
        self._inventory = None
        self._mark_pending()
        self.result_label.setText("Inspecting AGS source...")
        worker = AGSInspectWorker(
            Path(self.source_edit.text().strip()),
            self._selected_roles(),
            generation,
            refresh=refresh,
            parent=self,
        )
        self._workers.add(worker)
        worker.inspected.connect(self._accept_inspection)
        worker.finished.connect(self._worker_finished)
        self._update_buttons()
        worker.start()

    @Slot(int, object, str)
    def _accept_inspection(self, generation: int, inventory, error: str):
        if generation != self._generation:
            return
        if error:
            self.result_label.setText(f"Source check failed: {error}")
            return
        from emu68hatcher.builder.ags_requirements import calculate_ags_requirements

        try:
            requirements = calculate_ags_requirements(inventory.components)
        except ValueError as error:
            self.result_label.setText(f"Source check failed: {error}")
            return
        self._inventory = inventory
        lines = [f"AGS {inventory.version} ({inventory.profile})"]
        for requirement in requirements:
            lines.append(
                f"{requirement.role}: AGS partition copy, "
                f"{requirement.minimum_partition_bytes / 1024**3:.2f} GiB reserved"
            )
        lines.extend(inventory.warnings)
        self.result_label.setText("\n".join(lines))
        self._update_buttons()

    @Slot()
    def _worker_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        worker.deleteLater()
        self._update_buttons()

    def inspection_pending(self) -> bool:
        return bool(self._workers)

    def set_partitions(self, partitions: PartitionConfig):
        self._partitions = partitions
        self._revision += 1
        self._update_layout_label()
        self._update_buttons()

    def set_layout_error(self, error: str):
        self._partitions = None
        self._revision += 1
        self.layout_label.setText(f"Partition layout error: {error}")
        self._update_buttons()

    def preview_partitions(self):
        self._show_proposal(self._selected_roles())

    def _show_proposal(self, roles: tuple[AGSRole, ...]) -> bool:
        if self._partitions is None:
            return False
        if roles and self._inventory is None:
            self.result_label.setText("Inspect the selected source before planning partitions.")
            return False
        if self._inventory is not None:
            from emu68hatcher.builder.ags_requirements import calculate_ags_requirements

            requirements = {
                item.role: item for item in calculate_ags_requirements(self._inventory.components)
            }
            source_identity = self._inventory.identity
        else:
            requirements = {}
            source_identity = None
        dialog = AGSProposalDialog(
            self._partitions, requirements, roles, source_identity, self._revision, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if self._partitions is None:
            return False
        try:
            if roles:
                from emu68hatcher.builder.ags_source import source_identity as current_identity

                source_identity = current_identity(Path(self.source_edit.text().strip()))
            changed = apply_ags_layout(
                self._partitions,
                dialog.proposal,
                source_identity=source_identity,
                selection_revision=self._revision,
            )
        except Exception as error:
            self.layout_label.setText(str(error))
            return False
        self._partitions = changed
        self._allocation_state = "ready"
        self._legacy_content_device = None
        self._migration_notice = None
        self.layout_applied.emit(changed)
        self._update_layout_label()
        self._update_buttons()
        return True

    def set_config(self, config: AGSImportConfig | None):
        self._loading = True
        try:
            self._configured = config is not None
            self._committed_enabled = bool(config and config.enabled)
            self.enabled_check.setChecked(self._committed_enabled)
            self.source_edit.setText(str(config.source_image) if config else "")
            components = config.components if config else AGSComponents()
            for role in AGS_ROLES:
                self.component_checks[role].setChecked(getattr(components, role))
            self._allocation_state = config.allocation_state if config else "pending"
            self._legacy_content_device = config.legacy_content_device if config else None
            self._migration_notice = config.migration_notice if config else None
            self._inventory = None
            self._generation += 1
            for worker in self._workers:
                worker.requestInterruption()
        finally:
            self._loading = False
        self.result_label.setText(
            (config.migration_notice + "\n" if config and config.migration_notice else "")
            + "Inspect the source to calculate partition sizes."
        )
        self._update_layout_label()
        self._update_buttons()

    def get_config(self) -> dict | None:
        source = self.source_edit.text().strip()
        if not source and not self.enabled_check.isChecked() and not self._has_reservations():
            return None
        if not source:
            raise ValueError("Select an AGS source image")
        return {
            "source_image": source,
            "enabled": self.enabled_check.isChecked(),
            "components": {role: self.component_checks[role].isChecked() for role in AGS_ROLES},
            "allocation_state": self._allocation_state,
            "legacy_content_device": self._legacy_content_device,
            "migration_notice": self._migration_notice,
        }

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        workers = tuple(worker for worker in self._workers if worker.isRunning())
        for worker in workers:
            worker.requestInterruption()
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)
