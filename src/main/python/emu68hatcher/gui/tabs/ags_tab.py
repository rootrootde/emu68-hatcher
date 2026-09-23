"""AGS image selection and source inspection."""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.partition_helpers import usable_partition_content_size
from emu68hatcher.config.partition_models import PartitionConfig
from emu68hatcher.config.schema import AGSImportConfig


class AGSInspectWorker(QThread):
    inspected = Signal(int, object, str)

    def __init__(self, source_path: Path, generation: int, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.generation = generation

    def run(self):
        try:
            from emu68hatcher.builder.ags_source import inspect_ags_source

            inventory = inspect_ags_source(
                self.source_path, cancel_check=self.isInterruptionRequested
            )
        except Exception as error:
            self.inspected.emit(self.generation, None, str(error) or type(error).__name__)
            return
        self.inspected.emit(self.generation, inventory, "")


class AGSTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: set[AGSInspectWorker] = set()
        self._generation = 0
        self._inventory = None
        self._partitions: PartitionConfig | None = None

        layout = QVBoxLayout(self)
        self.enabled_check = QCheckBox("Import AGS v3.0 WHDLoad games and launcher")
        layout.addWidget(self.enabled_check)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source image:"))
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Local AGS Classic AGA v3.0 image")
        self.source_edit.textChanged.connect(self._source_changed)
        source_row.addWidget(self.source_edit, 1)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse)
        source_row.addWidget(self.browse_btn)
        self.inspect_btn = QPushButton("Inspect Source")
        self.inspect_btn.clicked.connect(self.inspect_source)
        source_row.addWidget(self.inspect_btn)
        layout.addLayout(source_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Content partition:"))
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self._update_space)
        target_row.addWidget(self.device_combo, 1)
        layout.addLayout(target_row)

        self.result_label = QLabel("Source not inspected. The build checks the source again.")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.space_label = QLabel()
        self.space_label.setWordWrap(True)
        layout.addWidget(self.space_label)
        self.scope_label = QLabel(
            "First release: v3.0 WHDLoad + AGS only. Games, Premium and emulators are "
            "not supported. AGS keeps its WHDLoad dependency when Software is set to Minimal. "
            "The launcher targets native AGA on A1200/PiStorm; runtime behavior is unverified."
        )
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        layout.addStretch()
        self._source_changed()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select AGS image", "", "Disk images (*.img *.hdf);;All Files (*)"
        )
        if path:
            self.source_edit.setText(path)

    def _source_changed(self):
        self._generation += 1
        self._inventory = None
        for worker in self._workers:
            worker.requestInterruption()
        self.result_label.setText("Source not inspected. The build checks the source again.")
        self._update_space()
        self._update_inspect_button()

    def _update_inspect_button(self):
        self.inspect_btn.setEnabled(bool(self.source_edit.text().strip()) and not self._workers)

    def inspect_source(self):
        if self._workers or not self.source_edit.text().strip():
            return
        source = Path(self.source_edit.text().strip())
        self._generation += 1
        generation = self._generation
        self._inventory = None
        self._update_space()
        self.result_label.setText("Inspecting AGS source...")
        worker = AGSInspectWorker(source, generation, self)
        self._workers.add(worker)
        worker.inspected.connect(self._accept_inspection)
        worker.finished.connect(self._worker_finished)
        self._update_inspect_button()
        worker.start()

    @Slot(int, object, str)
    def _accept_inspection(self, generation: int, inventory, error: str):
        if generation != self._generation:
            return
        if error:
            self.result_label.setText(f"Source check failed: {error}")
            return
        self._inventory = inventory
        version = str(inventory.version)
        self.result_label.setText(
            f"AGS {version} ({inventory.profile}): {inventory.file_count:,} files, "
            f"{self._format_size(inventory.content_bytes)} of content."
            + ("\n" + "\n".join(inventory.warnings) if inventory.warnings else "")
        )
        self._update_space()

    @Slot()
    def _worker_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        worker.deleteLater()
        self._update_inspect_button()

    def inspection_pending(self) -> bool:
        return bool(self._workers)

    @staticmethod
    def _format_size(size: int) -> str:
        return f"{size / 1024**3:.2f} GiB"

    def set_partitions(self, partitions: PartitionConfig):
        previous = self.device_combo.currentData()
        self._partitions = partitions
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for part in partitions.iter_amiga_partitions():
            self.device_combo.addItem(f"{part.device}: {part.volume}", part.device)
        if previous:
            index = self.device_combo.findData(previous)
            if index < 0:
                self.device_combo.addItem(f"{previous}: no longer in layout", previous)
                index = self.device_combo.count() - 1
            self.device_combo.setCurrentIndex(index)
        self.device_combo.blockSignals(False)
        self._update_space()

    def queue_partition_refresh(self, partition_provider):
        def refresh():
            try:
                partitions = partition_provider()
            except ValueError:
                return
            self.set_partitions(partitions)

        QTimer.singleShot(0, refresh)

    def _update_space(self):
        self.space_label.clear()
        if self._inventory is None or self._partitions is None:
            return
        device = self.device_combo.currentData()
        part = next(
            (part for part in self._partitions.iter_amiga_partitions() if part.device == device),
            None,
        )
        if part is None:
            self.space_label.setText("Select a partition in the current layout.")
            return
        available = usable_partition_content_size(part.size)
        required = self._inventory.required_bytes
        result = "fits before other content" if required <= available else "too small"
        self.space_label.setText(
            f"Estimated AGS space: {self._format_size(required)}; "
            f"{device} usable: {self._format_size(available)} ({result}). "
            "The build checks combined content and free space again."
        )

    def set_config(self, config: AGSImportConfig | None):
        self.enabled_check.setChecked(config is not None)
        self.source_edit.setText(str(config.source_image) if config else "")
        if config:
            index = self.device_combo.findData(config.content_device)
            if index < 0:
                self.device_combo.addItem(
                    f"{config.content_device}: no longer in layout", config.content_device
                )
                index = self.device_combo.count() - 1
            self.device_combo.setCurrentIndex(index)
        self._update_space()

    def get_config(self) -> dict | None:
        if not self.enabled_check.isChecked():
            return None
        source = self.source_edit.text().strip()
        device = self.device_combo.currentData()
        if not source:
            raise ValueError("Select an AGS source image")
        if not device:
            raise ValueError("Select an AGS content partition")
        if self._partitions is None or not any(
            part.device == device for part in self._partitions.iter_amiga_partitions()
        ):
            raise ValueError(f"AGS content partition {device} is not in the current layout")
        return {"source_image": source, "content_device": device, "scope": "whdload"}

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        workers = tuple(worker for worker in self._workers if worker.isRunning())
        for worker in workers:
            worker.requestInterruption()
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)
