"""Asset directory controls and background scan ownership."""

from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.gui.design import page_layout, set_tone
from emu68hatcher.gui.widgets.status_row import StatusRow
from emu68hatcher.gui.workers import ADFScanWorker, ROMScanWorker


class AssetScanPanel(QWidget):
    rom_results = Signal(list, bool)
    adf_results = Signal(list, bool)
    state_changed = Signal()

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self._version = version
        self._workers: set[ROMScanWorker | ADFScanWorker] = set()
        self._generation = 0
        self._active = 0
        self._rom_rows: list[tuple[str, str, str, str, str]] = []
        self._whdload_rows: list[tuple[str, str, str]] = []
        self._adf_rows: list[tuple[str, str, str, str, bool]] = []
        self._dialog_title = ""
        self._setup_ui()
        self.state_changed.connect(self._update_result_visibility)
        self.rom_results.connect(self._update_result_visibility)
        self.adf_results.connect(self._update_result_visibility)
        self._update_result_visibility()

    def _setup_ui(self) -> None:
        layout = page_layout(self)
        directory_group = QGroupBox("ROMs && installation media")
        directory_layout = QVBoxLayout(directory_group)
        self.dir_list = QListWidget()
        self.dir_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.dir_list.setFixedHeight(88)
        directory_layout.addWidget(self.dir_list)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("Add...")
        self.remove_button = QPushButton("Remove")
        self.rescan_button = QPushButton("Rescan")
        self.add_button.clicked.connect(self._add_directory)
        self.remove_button.clicked.connect(self._remove_directory)
        self.rescan_button.clicked.connect(self.scan)
        for button in (self.add_button, self.remove_button, self.rescan_button):
            buttons.addWidget(button)
        buttons.addStretch()
        directory_layout.addLayout(buttons)
        directory_group.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(directory_group)

        detected_group = QGroupBox("Detected files")
        detected_layout = QVBoxLayout(detected_group)
        detected_layout.setSpacing(12)
        self.hint = QLabel("Add a folder that contains your Kickstart ROMs and Workbench ADFs.")
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        set_tone(self.hint, "muted")
        detected_layout.addWidget(self.hint)
        self.rom_row = StatusRow("Kickstart ROM")
        self.whdload_row = StatusRow("WHDLoad Kickstarts")
        self.adf_row = StatusRow("Installation media")
        for row in (self.rom_row, self.whdload_row, self.adf_row):
            row.hide()
            detected_layout.addWidget(row)
        self.details_button = QPushButton("Show details…")
        self.details_button.setEnabled(False)
        self.details_button.clicked.connect(self._show_details)
        self.adf_row.trailing.addWidget(self.details_button)
        layout.addWidget(detected_group)

    @property
    def directories(self) -> list[Path]:
        return [Path(self.dir_list.item(index).text()) for index in range(self.dir_list.count())]

    @directories.setter
    def directories(self, paths: list[Path | str]) -> None:
        self.dir_list.clear()
        for value in dict.fromkeys(str(path) for path in paths):
            self.dir_list.addItem(QListWidgetItem(value))

    @property
    def scan_directories(self) -> list[Path]:
        return [path for path in self.directories if path.exists() and path.is_dir()]

    @property
    def results(self) -> dict[str, list[tuple]]:
        return {
            "roms": list(self._rom_rows),
            "whdload": list(self._whdload_rows),
            "media": list(self._adf_rows),
        }

    def set_version(self, version: str) -> None:
        self._version = version
        self.scan()

    def _add_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add directory containing ROMs / ADFs", "")
        if path and path not in {str(item) for item in self.directories}:
            self.dir_list.addItem(QListWidgetItem(path))
            self.scan()

    def _remove_directory(self) -> None:
        row = self.dir_list.currentRow()
        if row >= 0:
            self.dir_list.takeItem(row)
            self.scan()

    def scan(self) -> None:
        self._generation += 1
        generation = self._generation
        for worker in tuple(self._workers):
            if worker.isRunning():
                worker.requestInterruption()
        configured_directories = self.directories
        directories = self.scan_directories
        if not directories:
            self._rom_rows = []
            self._whdload_rows = []
            self._adf_rows = []
            self.adf_results.emit([], False)
            self._refresh_details_button()
            for row in (self.rom_row, self.whdload_row, self.adf_row):
                row.hide()
            if configured_directories:
                paths = "\n".join(str(path) for path in configured_directories)
                self.hint.setText(
                    f"Folder not available:\n{paths}\n"
                    "Connect the drive or choose another folder, then rescan."
                )
                set_tone(self.hint, "warning")
            else:
                self.hint.setText(
                    "Add a folder that contains your Kickstart ROMs and Workbench ADFs."
                )
                set_tone(self.hint, "muted")
            self.hint.show()
            self.state_changed.emit()
            return
        self._rom_rows = []
        self._whdload_rows = []
        self._adf_rows = []
        self.adf_results.emit([], False)
        self._dialog_title = ""
        self._refresh_details_button()
        self.hint.hide()
        self.rom_row.set_status("pending", "Kickstart ROM", "Scanning…")
        self.rom_row.show()
        self.whdload_row.hide()
        self.adf_row.set_status("pending", "Installation media", "Scanning…")
        self.adf_row.show()
        self._start_worker(ROMScanWorker(directories, self), generation, True)
        self._start_worker(ADFScanWorker(directories, self), generation, False)

    def _start_worker(self, worker, generation: int, rom: bool) -> None:
        self._workers.add(worker)
        self._set_active(1)
        if rom:
            worker.scan_finished.connect(
                lambda found, truncated, g=generation: self._accept_rom(g, found, truncated)
            )
            worker.scan_error.connect(lambda error, g=generation: self._scan_error(g, error, True))
        else:
            worker.scan_finished.connect(
                lambda found, truncated, g=generation: self._accept_adf(g, found, truncated)
            )
            worker.scan_error.connect(lambda error, g=generation: self._scan_error(g, error, False))
        worker.finished.connect(lambda w=worker: self._worker_finished(w))
        worker.start()

    def _accept_rom(self, generation: int, found: list, truncated: bool) -> None:
        if generation == self._generation:
            self._show_rom_results(found, truncated)
            self.rom_results.emit(found, truncated)

    def _accept_adf(self, generation: int, found: list, truncated: bool) -> None:
        if generation == self._generation:
            self._show_adf_results(found, truncated)
            self.adf_results.emit(found, truncated)

    def _scan_error(self, generation: int, message: str, rom: bool) -> None:
        if generation != self._generation:
            return
        row = self.rom_row if rom else self.adf_row
        row.set_status("error", None, f"Scan failed: {message}")
        self.state_changed.emit()

    def _worker_finished(self, worker) -> None:
        self._workers.discard(worker)
        self._set_active(-1)

    def _set_active(self, delta: int) -> None:
        self._active = max(0, self._active + delta)
        enabled = self._active == 0
        for button in (self.add_button, self.remove_button, self.rescan_button):
            button.setEnabled(enabled)
        self.state_changed.emit()

    def shutdown_workers(self, timeout_ms: int = 500) -> bool:
        workers = tuple(worker for worker in self._workers if worker.isRunning())
        for worker in workers:
            worker.requestInterruption()
        deadline = monotonic() + timeout_ms / 1000
        for worker in workers:
            worker.wait(max(0, int((deadline - monotonic()) * 1000)))
        return not any(worker.isRunning() for worker in workers)

    @Slot(list, bool)
    def _show_rom_results(self, found_roms: list, truncated: bool = False) -> None:
        from emu68hatcher.data.rom_detection import find_kickstart_for_version

        boot_path = (
            find_kickstart_for_version(self.scan_directories, self._version) if found_roms else None
        )
        self._rom_rows = self._build_rom_rows(found_roms, boot_path)
        self._update_whdload_status(found_roms)
        self._refresh_details_button()
        title = f"Kickstart {self._version} ROM"
        if not found_roms:
            detail = (
                "Scan stopped after too many files. Choose a narrower folder."
                if truncated
                else "No Kickstart ROMs found in these folders."
            )
            self.rom_row.set_status("error", title, detail)
            return
        if boot_path:
            boot_rom = next((rom for rom in found_roms if rom["path"] == boot_path), None)
            if boot_rom:
                self.rom_row.set_status("ok", title, f"{boot_path.name} · {boot_rom['model']}")
                return
        excluded = [
            rom for rom in found_roms if rom["version"] == self._version and rom.get("excluded")
        ]
        if excluded:
            message = excluded[0].get("exclude_message", "ROM is not supported")
            self.rom_row.set_status("error", title, f"Found, but not usable: {message}")
            return
        versions = sorted(
            {rom["version"] for rom in found_roms if not rom.get("excluded")},
            reverse=True,
        )
        self.rom_row.set_status(
            "error", title, f"Not found. ROMs for {', '.join(versions)} are available."
        )

    def _build_rom_rows(self, found_roms: list, boot_path) -> list[tuple]:
        rows = []
        for rom in found_roms:
            path = rom["path"]
            if rom.get("excluded"):
                status = "excluded"
            elif boot_path is not None and path == boot_path:
                status = "boot"
            elif rom["version"] == self._version:
                status = "available"
            else:
                status = "other_version"
            rows.append((status, path.name, rom["version"], rom.get("model", ""), str(path)))
        return rows

    def _update_whdload_status(self, found_roms: list) -> None:
        from emu68hatcher.data.rom_detection import WHDLOAD_ROM_NAMES

        paths: dict[str, str] = {}
        for rom in found_roms:
            name = rom.get("whdload_name")
            if name and not rom.get("excluded"):
                paths.setdefault(name, str(rom["path"]))
        self._whdload_rows = [
            ("found" if name in paths else "missing", name, paths.get(name, ""))
            for name in WHDLOAD_ROM_NAMES
        ]
        total = len(WHDLOAD_ROM_NAMES)
        found = len(paths)
        self.whdload_row.set_status(
            "ok" if found == total else "info",
            "WHDLoad Kickstarts",
            f"{found} of {total} found · copied to DEVS:Kickstarts" if found else "None found",
        )
        self.whdload_row.show()

    @Slot(list, bool)
    def _show_adf_results(self, found_media: list, truncated: bool = False) -> None:
        from emu68hatcher.data.install_media import get_required_install_media
        from emu68hatcher.data.package_loader import get_adf_rules_for_version

        self._adf_rows = []
        self._refresh_details_button()
        title = "AmigaOS 3.9 CD" if self._version == "3.9" else f"Workbench {self._version} ADFs"
        if not found_media:
            if truncated:
                detail = "Scan stopped after too many files. Choose a narrower folder."
            elif self._version == "3.9":
                detail = "Add the CD image (.iso) to one of these folders. Do not mount it."
            else:
                detail = "No Workbench ADFs found in these folders."
            self.adf_row.set_status("error", title, detail)
            return
        expected = {rule.adf for rule in get_adf_rules_for_version(self._version)}
        required = set(get_required_install_media(self._version))
        expected |= required
        optional = expected - required
        found_by_name = {}
        for media in found_media:
            found_by_name.setdefault(media.adf_name, media)
        required_found = required & found_by_name.keys()
        optional_found = optional & found_by_name.keys()
        required_missing = required - found_by_name.keys()
        for adf_name in sorted(expected):
            is_required = adf_name in required
            media = found_by_name.get(adf_name)
            if media is not None:
                status = "found"
                friendly = media.friendly_name or adf_name
                version = media.workbench_version or ""
            else:
                status = "missing_required" if is_required else "missing_optional"
                friendly, version = self._infer_adf_labels(adf_name)
            self._adf_rows.append((status, adf_name, friendly, version, is_required))
        self._dialog_title = f"Detected files for Workbench {self._version}"
        self._refresh_details_button()
        if self._version == "3.9":
            if required_missing:
                self.adf_row.set_status(
                    "error",
                    title,
                    "Add the CD image (.iso) to one of these folders. Do not mount it.",
                )
            else:
                self.adf_row.set_status(
                    "ok", title, "Found · BoingBags are downloaded during the build"
                )
            return
        detail = (
            f"{len(required_found)} of {len(required)} required · "
            f"{len(optional_found)} of {len(optional)} optional"
        )
        if required_missing:
            names = ", ".join(self._infer_adf_labels(name)[0] for name in sorted(required_missing))
            detail += f"\nMissing: {names}"
        self.adf_row.set_status("error" if required_missing else "ok", title, detail)

    def _update_result_visibility(self, *_args):
        self.details_button.setVisible(self.details_button.isEnabled())

    def _refresh_details_button(self) -> None:
        self.details_button.setEnabled(bool(self._rom_rows or self._whdload_rows or self._adf_rows))

    def _show_details(self) -> None:
        if not self.details_button.isEnabled():
            return
        from emu68hatcher.gui.dialogs import DetectedFilesDialog

        dialog = DetectedFilesDialog(
            title=self._dialog_title or f"Detected files for Workbench {self._version}",
            rom_rows=self._rom_rows,
            whdload_rows=self._whdload_rows,
            adf_rows=self._adf_rows,
            parent=self,
        )
        dialog.exec()

    @staticmethod
    def _infer_adf_labels(adf_name: str) -> tuple[str, str]:
        import re

        match = re.match(r"^(.*?)(\d+(?:_\d+)+)$", adf_name)
        if not match:
            return adf_name, ""
        base, version = match.group(1), match.group(2).replace("_", ".")
        return f"{base} {version}", version
