from types import SimpleNamespace

import pytest
from emu68hatcher.config.ags_layout import plan_ags_layout
from emu68hatcher.config.loader import load_config
from emu68hatcher.config.schema import BuildConfig
from emu68hatcher.gui.partition_editor_model import PartitionEditorModel


def _editor():
    model = PartitionEditorModel(64)
    assert model.add_partition()
    model.set_device(0, "SDH5")
    model.set_volume(0, "Data")
    model.set_device(1, "SDH3")
    return model


@pytest.mark.parametrize("duplicate", ["Data", "data"])
def test_save_names_conflicts_and_succeeds_after_rename(monkeypatch, tmp_path, capsys, duplicate):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.main_window import MainWindow, QFileDialog, QMessageBox

    model = _editor()
    window = SimpleNamespace(config=BuildConfig(partitions=model.to_config()))
    window.collect_config = lambda: setattr(window.config, "partitions", model.to_config())
    status = []
    window.statusBar = lambda: SimpleNamespace(showMessage=status.append)
    path = tmp_path / "config.json"
    path.write_text("existing file")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: pytest.fail("validation should show an actionable warning"),
    )
    model.set_volume(1, duplicate)

    MainWindow.save_config_file(window)

    assert len(warnings) == 1
    assert "'Data' on SDH5, SDH3" in warnings[0]
    assert "Partitions tab" in warnings[0]
    assert "input_value" not in warnings[0]
    assert "errors.pydantic.dev" not in warnings[0]
    assert not capsys.readouterr().err
    assert path.read_text() == "existing file"
    assert not status

    model.set_volume(1, "Data_1")
    MainWindow.save_config_file(window)

    assert status == [f"Saved: {path}"]
    assert len(warnings) == 1
    assert [p.volume for p in load_config(path).partitions.iter_amiga_partitions()] == [
        "Data",
        "Data_1",
    ]


def test_ags_layout_reports_volume_conflict_without_validation_dump():
    model = _editor()
    model.set_volume(1, "Data")

    proposal = plan_ags_layout(model.to_layout_draft(), {}, ())

    assert len(proposal.errors) == 1
    assert "'Data' on SDH5, SDH3" in proposal.errors[0]
    assert "input_value" not in proposal.errors[0]
    assert "errors.pydantic.dev" not in proposal.errors[0]


def test_volume_edit_updates_selected_partition_details(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.tabs.partitions import PartitionsTab
    from emu68hatcher.gui.widgets.partition_table import COL_VOLUME
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    tab = PartitionsTab()
    tab.set_config(_editor().to_config())
    tab.part_table.selectRow(1)

    tab.part_table.item(1, COL_VOLUME).setText("Data")

    assert "SDH3 (Data)" in tab._extras_label.text()
    assert "'Data' on SDH5, SDH3" in tab.error_label.text()

    tab.part_table.item(1, COL_VOLUME).setText("Data_1")

    assert "SDH3 (Data_1)" in tab._extras_label.text()
    assert not tab.error_label.text()
    tab.get_config()
    tab.deleteLater()
    app.processEvents()
