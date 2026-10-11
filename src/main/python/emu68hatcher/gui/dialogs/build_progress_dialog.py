"""build progress dialog - live pipeline progress, log toggle, eject flow"""

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from emu68hatcher.builder.stage_registry import PROGRESS_STAGE_ORDER, STAGE_LABELS
from emu68hatcher.config.schema import BuildConfig, OutputType
from emu68hatcher.gui.design import SECTION_GAP, apply_design, set_tone, style_sections
from emu68hatcher.gui.workers import BuildWorker
from emu68hatcher.utils.platform import OperatingSystem, get_platform_info


class _EjectWorker(QThread):
    """run the (blocking) disk eject off the UI thread"""

    done = Signal(bool, str)

    def __init__(self, device: str, parent=None):
        super().__init__(parent)
        self._device = device

    def run(self):
        from emu68hatcher.builder.host.disk_enum import eject_disk

        ok, msg = eject_disk(self._device)
        self.done.emit(ok, msg)


class BuildProgressDialog(QDialog):
    """live build progress dialog"""

    def __init__(self, config: BuildConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.worker: BuildWorker | None = None
        self._success: bool = False
        self._overall: float = 0.0  # monotonic overall %, so the bar never jumps back
        self.setup_ui()

    @property
    def success(self) -> bool:
        return self._success

    def setup_ui(self):
        self.setWindowTitle("Building Image...")
        self.setMinimumWidth(600)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(SECTION_GAP)

        self.title_label = QLabel("Building image")
        self.title_label.setProperty("heading", "page")
        layout.addWidget(self.title_label)
        description = QLabel("Follow the build progress. Open the log for details.")
        description.setProperty("tone", "muted")
        description.setWordWrap(True)
        layout.addWidget(description)

        progress_group = QGroupBox("Build progress")
        progress_layout = QVBoxLayout(progress_group)
        layout.addWidget(progress_group)

        # stylesheets so the bar heights are honoured (native macOS ignores setFixedHeight)
        overall_css = (
            "QProgressBar { border: 1px solid palette(mid); border-radius: 4px;"
            " background: palette(base); }"
            "QProgressBar::chunk { background-color: palette(highlight); border-radius: 3px; }"
        )
        self._step_css = (
            "QProgressBar { border: none; border-radius: 3px; background: palette(mid); }"
            "QProgressBar::chunk { background-color: palette(highlight); border-radius: 3px; }"
        )

        # overall progress across the whole pipeline (prominent: bold label + chunky bar)
        self.overall_label = QLabel("Overall  0%")
        self.overall_label.setProperty("heading", "brand")
        progress_layout.addWidget(self.overall_label)
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setTextVisible(False)
        self.overall_bar.setFixedHeight(18)
        self.overall_bar.setStyleSheet(overall_css)
        progress_layout.addWidget(self.overall_bar)

        progress_layout.addSpacing(6)

        # progress within the current step (subtle: gray label + thin bar)
        self.stage_label = QLabel("Initializing")
        self.stage_label.setProperty("tone", "muted")
        progress_layout.addWidget(self.stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(7)
        self.progress_bar.setStyleSheet(self._step_css)
        progress_layout.addWidget(self.progress_bar)

        progress_layout.addSpacing(6)

        # current action
        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        progress_layout.addWidget(self.status_label)

        # collapsible log - hidden by default
        self.log_toggle = QPushButton("▸ Show log")
        self.log_toggle.clicked.connect(self._toggle_log)
        layout.addWidget(self.log_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_output.setStyleSheet(
            "QTextEdit { background: palette(base); color: palette(text);"
            " border: 1px solid palette(mid); border-radius: 5px; padding: 8px; }"
        )
        self.log_output.setMinimumHeight(220)
        self.log_output.setVisible(False)
        layout.addWidget(self.log_output)

        # buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_build)
        btn_layout.addWidget(self.cancel_btn)

        # shown only after a successful flash to a removable device (macos/linux)
        self.eject_btn = QPushButton("Eject SD Card")
        self.eject_btn.clicked.connect(self._on_eject)
        self.eject_btn.setVisible(False)
        btn_layout.addWidget(self.eject_btn)
        self._eject_worker: _EjectWorker | None = None

        self.close_btn = QPushButton("Close")
        self.close_btn.setProperty("primary", True)
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setEnabled(False)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)
        style_sections(self)
        apply_design(self)
        # Resolve colors before Qt applies widget-local stylesheets on first show.
        for role in ("base", "text", "mid", "highlight"):
            color = self.palette().color(getattr(QPalette.ColorRole, role.title())).name()
            overall_css = overall_css.replace(f"palette({role})", color)
            self._step_css = self._step_css.replace(f"palette({role})", color)
            self.log_output.setStyleSheet(
                self.log_output.styleSheet().replace(f"palette({role})", color)
            )
        self.overall_bar.setStyleSheet(overall_css)
        self.progress_bar.setStyleSheet(self._step_css)

    def _toggle_log(self):
        """show/hide the log and resize the dialog to fit"""
        show = not self.log_output.isVisible()
        self.log_output.setVisible(show)
        self.log_toggle.setText("▾ Hide log" if show else "▸ Show log")
        self.adjustSize()

    def start_build(self):
        """kick off the build worker"""
        self.worker = BuildWorker(self.config, self)
        self.worker.progress_updated.connect(self.on_progress)
        self.worker.log_event.connect(self.on_log_event)
        self.worker.build_finished.connect(self.on_finished)
        self.worker.start()

    _STAGE_NAMES = STAGE_LABELS
    _STAGE_ORDER = PROGRESS_STAGE_ORDER

    @Slot(str, float, str)
    def on_progress(self, stage: str, progress: float, message: str):
        """transient status update - per-step bar + overall bar + status label"""
        name = self._STAGE_NAMES.get(stage, stage.title())
        # animate until the first flash progress report arrives
        if stage == "flash" and progress <= 0.0:
            # native bar (no stylesheet) animates the marquee reliably; styled ones don't on macOS
            self.progress_bar.setStyleSheet("")
            self.progress_bar.setRange(0, 0)  # indeterminate marquee
            self.stage_label.setText(name)
        else:
            if not self.progress_bar.styleSheet():
                self.progress_bar.setStyleSheet(self._step_css)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(progress))
            self.stage_label.setText(f"{name}  {int(progress)}%")
        self.status_label.setText(message)
        self.status_label.setToolTip(
            "Speed measures image data processed over the last 20 seconds, including skipped "
            "empty ranges. Remaining time is an estimate."
            if stage == "flash"
            else ""
        )
        # overall = how far through the whole pipeline; clamp monotonic so it never dips
        if stage in self._STAGE_ORDER:
            idx = self._STAGE_ORDER.index(stage)
            overall = (idx + progress / 100.0) / len(self._STAGE_ORDER) * 100.0
            self._overall = max(self._overall, overall)
            self.overall_bar.setValue(round(self._overall))
            self.overall_label.setText(f"Overall  {round(self._overall)}%")

    @Slot(str, str)
    def on_log_event(self, stage: str, message: str):
        """append one log entry"""
        self.log_output.append(f"[{stage}] {message}")

    @Slot(bool, str, str)
    def on_finished(self, success: bool, output_path: str, error: str):
        """build done - update labels + buttons"""
        self._success = success
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        # stop any flash marquee and restore the styled look
        if not self.progress_bar.styleSheet():
            self.progress_bar.setStyleSheet(self._step_css)
        self.progress_bar.setRange(0, 100)

        if success:
            self.setWindowTitle("Build Complete")
            self.title_label.setText("Build complete")
            set_tone(self.stage_label, "success")
            self._overall = 100.0
            self.overall_bar.setValue(100)
            self.overall_label.setText("Overall  100%")
            self.progress_bar.setValue(100)
            self.stage_label.setText("Done")
            self.status_label.setText(f"Output: {output_path}")
            self.log_output.append(f"\nBuild successful!\nOutput: {output_path}")
            if self._flash_device() and self._eject_supported():
                self.eject_btn.setVisible(True)
                self.eject_btn.setEnabled(True)
        else:
            self.setWindowTitle("Build Failed")
            self.title_label.setText("Build failed")
            set_tone(self.stage_label, "warning")
            self.stage_label.setText("Failed")
            self.status_label.setText(error)
            set_tone(self.status_label, "warning")
            self.log_output.append(f"\nBuild failed: {error}")
            # surface the log automatically on failure so the error context is visible
            if not self.log_output.isVisible():
                self._toggle_log()

    def _flash_device(self) -> str | None:
        """the physical SD device this build wrote to, or None if it produced a plain image"""
        out = self.config.output
        if not out:
            return None
        if out.flash_target:
            return str(out.flash_target)
        if out.type == OutputType.DEVICE:
            return str(out.path)
        return None

    @staticmethod
    def _eject_supported() -> bool:
        return get_platform_info().os in (OperatingSystem.MACOS, OperatingSystem.LINUX)

    def _on_eject(self):
        device = self._flash_device()
        if not device:
            return
        self.eject_btn.setEnabled(False)
        self.eject_btn.setText("Ejecting…")
        set_tone(self.status_label, "")
        self.status_label.setText(f"Ejecting {device}…")
        self._eject_worker = _EjectWorker(device, self)
        self._eject_worker.done.connect(self._on_eject_done)
        self._eject_worker.start()

    @Slot(bool, str)
    def _on_eject_done(self, ok: bool, msg: str):
        self.log_output.append(f"\n{msg}")
        if ok:
            self.eject_btn.setText("Ejected ✓")
            self.status_label.setText(f"{msg} - safe to remove the card")
        else:
            self.eject_btn.setText("Eject SD Card")
            self.eject_btn.setEnabled(True)
            set_tone(self.status_label, "warning")
            self.status_label.setText(f"Eject failed: {msg}")

    def cancel_build(self):
        """ask the worker to cancel"""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status_label.setText("Cancelling...")
            self.log_output.append("Cancellation requested...")

    def reject(self):
        """Esc lands here directly, skipping closeEvent - keep the same running-worker guard"""
        if self.worker and self.worker.isRunning():
            self.cancel_build()
            return
        super().reject()

    def closeEvent(self, event):
        """block close while the worker runs - Qt crashes if the dialog tears down mid-thread"""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            if not self.worker.wait(5000):
                self.status_label.setText(
                    "Build is still cancelling - please wait a moment and try again"
                )
                event.ignore()
                return
        # eject can't be cancelled; just wait it out so the QThread isn't torn down mid-run
        if self._eject_worker and self._eject_worker.isRunning():
            if not self._eject_worker.wait(10000):
                self.status_label.setText("Still ejecting - please wait a moment and try again")
                event.ignore()
                return
        super().closeEvent(event)
