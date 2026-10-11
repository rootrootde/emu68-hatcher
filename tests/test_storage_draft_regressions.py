"""Regressions for capacity, loading and extra-file draft boundaries."""


def test_capacity_changes_keep_custom_boot_size():
    from emu68hatcher.gui.partition_editor_model import PartitionEditorModel

    model = PartitionEditorModel(64)
    model.set_boot_size_mb(512)
    boot_size = model.boot_size
    sizes = [part.size for part in model.partitions]
    model.change_disk_size(4 * 1024**3)
    assert model.boot_size == boot_size
    assert [part.size for part in model.partitions] == sizes
    assert model.errors


def test_reset_keeps_extra_folder_on_renamed_boot_partition(tmp_path):
    from emu68hatcher.gui.partition_editor_model import PartitionEditorModel

    model = PartitionEditorModel(64)
    model.set_device(0, "SDF0")
    model.set_extra_directory(0, tmp_path)
    model.reset(disk_size_bytes=model.disk_size, preserve_extra_directories=True)
    assert model.partitions[0].bootable
    assert model.partitions[0].extra_content_directory == tmp_path


def test_loading_empty_output_clears_previous_image_path(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.tabs.output import OutputTab
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    tab = OutputTab()
    tab.output_path.setText("   ")
    assert tab.get_config()["path"] == ""
    tab.output_path.setText("/tmp/old.img")
    tab.set_config(None)
    assert tab.get_config()["path"] == ""
    tab.deleteLater()
    app.processEvents()


def test_missing_extra_folder_blocks_copy(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.storage_controller import StorageController
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    controller = StorageController()
    controller.model.set_extra_directory(0, tmp_path / "missing")
    assert controller.extras.status(controller.model.partitions[0])[1] == "error"
    assert controller.extras.errors()
    controller.deleteLater()
    app.processEvents()


def test_layout_refresh_keeps_uncommitted_partition_editor(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.tabs.partitions import PartitionsTab
    from PySide6.QtWidgets import QApplication, QLineEdit

    app = QApplication.instance() or QApplication([])
    tab = PartitionsTab()
    tab.show()
    app.processEvents()
    tab.part_table.setCurrentCell(0, 1)
    tab.part_table.editItem(tab.part_table.item(0, 1))
    app.processEvents()
    editor = app.focusWidget()
    assert isinstance(editor, QLineEdit)
    editor.setText("unfinished name")
    tab.render_snapshot()
    app.processEvents()
    editor = app.focusWidget()
    assert isinstance(editor, QLineEdit)
    assert editor.text() == "unfinished name"
    tab.close()
    tab.deleteLater()
    app.processEvents()


def test_load_handles_incomplete_network_and_rolls_back_failed_apply(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HATCHER_HOME", str(tmp_path))
    from emu68hatcher.config.defaults import create_default_config
    from emu68hatcher.gui.main_window import MainWindow
    from emu68hatcher.gui.tabs.start import StartTab
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(StartTab, "check_for_updates", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.network_tab.eth_static.setChecked(True)
    window.network_tab.eth_addr.setText("123.")
    window.controller.load(create_default_config())
    assert window.network_tab.eth_dhcp.isChecked()
    window.network_tab.eth_static.setChecked(True)
    window.network_tab.eth_addr.setText("123.")
    window.output_tab.output_path.setText("/tmp/before.img")
    layout = window.storage_controller.model.to_layout_draft().model_dump()

    def fail_after_partial_apply(_config):
        window.network_tab.eth_addr.setText("192.168.1.5")
        window.output_tab.output_path.setText("/tmp/after.img")
        raise ValueError("setter failed")

    monkeypatch.setattr(window, "_apply_config", fail_after_partial_apply)
    import pytest

    with pytest.raises(ValueError, match="setter failed"):
        window.controller.load(create_default_config())
    assert window.output_tab.output_path.text() == "/tmp/before.img"
    assert window.network_tab.eth_addr.text() == "123."
    assert window.network_tab.eth_static.isChecked()
    assert window.storage_controller.model.to_layout_draft().model_dump() == layout
    window.close()
    window.deleteLater()
    app.processEvents()
