"""AGS content selection and live partition layout."""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.ags_layout import AGS_VOLUMES, apply_ags_layout, plan_ags_layout
from emu68hatcher.config.ags_models import AGS_ROLES, AGSComponents, AGSRole
from emu68hatcher.config.partition_helpers import calculate_free_space
from emu68hatcher.config.partition_models import PartitionConfig
from emu68hatcher.config.schema import AGSImportConfig
from emu68hatcher.gui.widgets.partition_bar import PartitionBar


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


class AGSTab(QWidget):
    layout_applied = Signal(object)
    partitions_requested = Signal()
    target_requested = Signal()

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
        self._loading = False
        self._syncing = False
        self._accepted_roles = AGSComponents().selected_roles()
        self._inspection_requested = False
        self._force_refresh = False
        self._inspect_timer = QTimer(self)
        self._inspect_timer.setSingleShot(True)
        self._inspect_timer.setInterval(350)
        self._inspect_timer.timeout.connect(self._start_inspection)

        layout = QVBoxLayout(self)
        self.enabled_check = QCheckBox("Import AGS")
        self.enabled_check.toggled.connect(self._enabled_changed)
        layout.addWidget(self.enabled_check)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source image:"))
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Choose an AGS .img or .hdf image")
        self.source_edit.textChanged.connect(self._source_changed)
        source_row.addWidget(self.source_edit)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        source_row.addWidget(browse)
        self.inspect_btn = QPushButton("Refresh")
        self.inspect_btn.clicked.connect(self.inspect_source)
        source_row.addWidget(self.inspect_btn)
        layout.addLayout(source_row)
        self.result_label = QLabel("Choose an image to see its content and partition sizes.")
        self.result_label.setWordWrap(True)
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.result_label)

        self.component_checks = {}
        self.component_sizes = {}
        for role, label, checked in (
            ("whdload", "WHDLoad games, demos and AGS (required)", True),
            ("games", "Extra games and Premium", True),
            ("work", "Emulators and applications", True),
            ("media", "Media", False),
        ):
            row = QHBoxLayout()
            check = QCheckBox(label)
            check.setChecked(checked)
            check.toggled.connect(self._selection_changed)
            self.component_checks[role] = check
            row.addWidget(check)
            row.addStretch()
            size = QLabel("—")
            self.component_sizes[role] = size
            row.addWidget(size)
            layout.addLayout(row)

        self.layout_label = QLabel()
        self.layout_label.setWordWrap(True)
        self.layout_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.layout_label)
        self.partition_bar = PartitionBar(interactive=False)
        layout.addWidget(self.partition_bar)
        hint = QLabel(
            "Content choices update the planned partitions automatically. "
            "Nothing is written until you build."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.target_btn = QPushButton("Change target size…")
        self.target_btn.clicked.connect(self.target_requested.emit)
        actions.addWidget(self.target_btn)
        self.partitions_btn = QPushButton("Adjust partitions…")
        self.partitions_btn.clicked.connect(self.partitions_requested.emit)
        actions.addWidget(self.partitions_btn)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self._refresh_layout()

    def _selected_roles(self) -> tuple[AGSRole, ...]:
        return tuple(role for role in AGS_ROLES if self.component_checks[role].isChecked())

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select AGS image", "", "Disk images (*.img *.hdf);;All Files (*)"
        )
        if path:
            self.source_edit.setText(path)
            self.enabled_check.setChecked(True)

    def _cancel_inspection(self):
        self._generation += 1
        self._inspect_timer.stop()
        self._inspection_requested = False
        for worker in self._workers:
            worker.requestInterruption()

    def _schedule_inspection(self):
        if self.enabled_check.isChecked() and self.source_edit.text().strip():
            self._inspection_requested = True
            self._inspect_timer.start()
            self.result_label.setText("Checking AGS source…")
        self._update_buttons()

    def _source_changed(self):
        if self._loading:
            return
        self._cancel_inspection()
        self._inventory = None
        self._force_refresh = False
        self._allocation_state = "pending"
        self.result_label.setText("Choose an image to see its content and partition sizes.")
        self._schedule_inspection()
        self._refresh_layout()

    def _confirm_removal(self, roles):
        if self._partitions is None:
            return True
        affected = [
            part.volume
            for part in self._partitions.iter_amiga_partitions()
            if part.ags_reservation
            and part.ags_reservation.role not in roles
            and part.extra_content_directory is not None
        ]
        if not affected:
            return True
        return (
            QMessageBox.question(
                self,
                "Remove AGS content",
                "Removing " + ", ".join(affected) + " also removes their extra-content folder "
                "settings from this build configuration. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _selection_changed(self):
        if self._loading:
            return
        roles = self._selected_roles()
        if not self._confirm_removal(roles):
            for role, check in self.component_checks.items():
                check.blockSignals(True)
                check.setChecked(role in self._accepted_roles)
                check.blockSignals(False)
            return
        self._accepted_roles = roles
        self._cancel_inspection()
        available = {item.role for item in self._inventory.components} if self._inventory else set()
        if not set(roles).issubset(available):
            self._allocation_state = "pending"
            self._schedule_inspection()
        self._refresh_layout()

    def _enabled_changed(self, enabled: bool):
        if self._loading:
            return
        if not enabled and not self._confirm_removal(()):
            self.enabled_check.blockSignals(True)
            self.enabled_check.setChecked(True)
            self.enabled_check.blockSignals(False)
            return
        self._cancel_inspection()
        available = {item.role for item in self._inventory.components} if self._inventory else set()
        if enabled and not set(self._selected_roles()).issubset(available):
            self._schedule_inspection()
        self._refresh_layout()

    def _has_reservations(self) -> bool:
        return self._partitions is not None and any(
            part.ags_reservation for part in self._partitions.iter_amiga_partitions()
        )

    def _update_buttons(self):
        enabled = self.enabled_check.isChecked()
        self.inspect_btn.setEnabled(
            enabled and bool(self.source_edit.text().strip()) and not self.inspection_pending()
        )
        for role, check in self.component_checks.items():
            check.setEnabled(enabled and role != "whdload")
        sizes = (
            {p.volume.casefold(): p.size for p in self._inventory.partitions}
            if self._inventory
            else {}
        )
        for role, label in self.component_sizes.items():
            size = sizes.get(AGS_VOLUMES[role].casefold())
            label.setText(f"{size / 1024**3:.2f} GiB" if size is not None else "—")

    def inspect_source(self):
        self._cancel_inspection()
        self._force_refresh = True
        self._inventory = None
        self._allocation_state = "pending"
        self._schedule_inspection()
        self._refresh_layout()

    def _start_inspection(self):
        if not self._inspection_requested or self._workers:
            return
        self._inspection_requested = False
        if not self.enabled_check.isChecked() or not self.source_edit.text().strip():
            return
        worker = AGSInspectWorker(
            Path(self.source_edit.text().strip()),
            self._selected_roles(),
            self._generation,
            refresh=self._force_refresh,
            parent=self,
        )
        self._force_refresh = False
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
            self._inventory = None
            self._allocation_state = "pending"
            self.result_label.setText(f"Source check failed: {error}")
        else:
            self._inventory = inventory
            self.result_label.setText("\n".join([f"AGS {inventory.version}", *inventory.warnings]))
        self._refresh_layout()

    @Slot()
    def _worker_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        worker.deleteLater()
        if self._inspection_requested:
            self._inspect_timer.start()
        self._update_buttons()

    def inspection_pending(self) -> bool:
        return bool(self._workers) or self._inspection_requested or self._inspect_timer.isActive()

    def set_partitions(self, partitions: PartitionConfig, *, update=True):
        self._partitions = partitions.model_copy(deep=True)
        self._revision += 1
        if update and not self._syncing:
            self._refresh_layout()

    def _refresh_layout(self):
        self._update_buttons()
        if self._partitions is None:
            self._allocation_state = "pending"
            self.partition_bar.hide()
            return
        enabled = self.enabled_check.isChecked()
        roles = self._selected_roles() if enabled else ()
        requirements = (
            {item.role: item.partition.size for item in self._inventory.components}
            if self._inventory
            else {}
        )
        self.partition_bar.show()
        self.partition_bar.set_data(
            self._partitions.layout[0].size,
            list(self._partitions.iter_amiga_partitions()),
            calculate_free_space(
                self._partitions.layout[1].size, list(self._partitions.iter_amiga_partitions())
            ),
        )
        if enabled and (self._inventory is None or not set(roles).issubset(requirements)):
            self._allocation_state = "pending"
            self.layout_label.setText(
                "Partition sizes will update when the source check finishes."
                if self.inspection_pending()
                else "Select a readable AGS image to continue."
            )
            return
        identity = self._inventory.identity if enabled else None
        proposal = plan_ags_layout(
            self._partitions,
            requirements,
            roles,
            source_identity=identity,
            selection_revision=self._revision,
        )
        if proposal.errors:
            # keep the current disk size and selected imports visible while the user fixes capacity
            changed = self._partitions.model_copy(deep=True)
            changed.layout[1].amiga_partitions = list(proposal.partitions)
            self._allocation_state = "pending"
        else:
            changed = apply_ags_layout(
                self._partitions,
                proposal,
                source_identity=identity,
                selection_revision=self._revision,
            )
            self._allocation_state = "ready"
            self._legacy_content_device = None
            self._migration_notice = None
        selected_bytes = sum(requirements[role] for role in roles)
        lines = (
            [f"Selected AGS content: {selected_bytes / 1024**3:.2f} GiB"]
            if enabled
            else ["AGS import is off."]
        )
        if proposal.free_bytes < 0:
            lines.append(
                f"Selected content exceeds available space by {-proposal.free_bytes / 1024**3:.2f} GiB. "
                "Deselect content, choose a larger target, or adjust your partitions."
            )
        else:
            lines.append(f"Space remaining: {proposal.free_bytes / 1024**3:.2f} GiB")
        lines.extend(error for error in proposal.errors if "exceeds RDB capacity" not in error)
        if self._migration_notice:
            lines.append(self._migration_notice)
        self.layout_label.setText("\n".join(lines))
        self.partition_bar.set_data(
            changed.layout[0].size, list(proposal.partitions), proposal.free_bytes
        )
        differs = changed.model_dump() != self._partitions.model_dump()
        self._partitions = changed
        if differs:
            self._syncing = True
            try:
                self.layout_applied.emit(changed)
            finally:
                self._syncing = False

    def set_config(self, config: AGSImportConfig | None):
        self._cancel_inspection()
        self._loading = True
        try:
            self.enabled_check.setChecked(bool(config and config.enabled))
            self.source_edit.setText(str(config.source_image) if config else "")
            components = config.components if config else AGSComponents()
            for role in AGS_ROLES:
                self.component_checks[role].setChecked(getattr(components, role))
            self._accepted_roles = components.selected_roles()
            self._allocation_state = "pending"
            self._legacy_content_device = config.legacy_content_device if config else None
            self._migration_notice = config.migration_notice if config else None
            self._inventory = None
            self._force_refresh = False
        finally:
            self._loading = False
        self.result_label.setText("Choose an image to see its content and partition sizes.")
        self._schedule_inspection()
        self._refresh_layout()

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
        self._cancel_inspection()
        workers = tuple(worker for worker in self._workers if worker.isRunning())
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)
