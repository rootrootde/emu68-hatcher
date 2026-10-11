def test_disabled_empty_ags_does_not_block_normal_config(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.tabs.ags_tab import AGSTab
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    tab = AGSTab()
    from emu68hatcher.config.partition_helpers import create_default_partition_layout

    tab.controller.set_partitions(create_default_partition_layout(64))
    tab.enabled_check.setChecked(True)
    tab.source_edit.setText("/missing/ags.img")
    tab.enabled_check.setChecked(False)
    assert tab.get_config()["enabled"] is False
    tab.source_edit.clear()
    assert tab.get_config() is None
    tab.deleteLater()
    app.processEvents()


def test_invalid_layout_cannot_restore_cached_disk_size(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.config.ags_layout import apply_ags_layout, plan_ags_layout
    from emu68hatcher.config.partition_helpers import round_to_cylinder
    from emu68hatcher.gui.tabs.ags_tab import AGSTab
    from emu68hatcher.gui.tabs.partitions import PartitionsTab
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    editor = PartitionsTab()
    tab = AGSTab(controller=editor.controller)
    original = editor.get_config()
    proposal = plan_ags_layout(original, {"whdload": round_to_cylinder(16 * 1024**3)}, ("whdload",))
    # Restore partitions and enabled import as one coordinated load.
    editor.set_config(apply_ags_layout(original, proposal), update=False)
    tab.enabled_check.setChecked(True)
    assert tab.controller.model is editor._model
    editor._model.change_disk_size(4 * 1024**3)
    editor._refresh_table()
    assert tab.controller.model.disk_size == 4 * 1024**3
    assert editor._model.errors

    tab.enabled_check.setChecked(False)

    assert editor._model.disk_size == 4 * 1024**3
    assert not any(part.ags_reservation for part in tab.controller.model.partitions)
    assert editor._model.partitions[0].size == original.layout[1].amiga_partitions[0].size
    editor._model.set_partition_size_mb(0, 2048)
    editor._refresh_table()
    editor.get_config()
    tab.shutdown_workers()
    tab.deleteLater()
    editor.deleteLater()
    app.processEvents()


def test_old_inspection_cannot_replace_new_source_selection(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.config.partition_helpers import create_default_partition_layout
    from emu68hatcher.gui.tabs.ags_tab import AGSTab
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    tab = AGSTab()
    tab.controller.set_partitions(create_default_partition_layout(64))
    tab.enabled_check.setChecked(True)
    tab.source_edit.setText("/old.img")
    generation = tab.controller.generation
    tab.source_edit.setText("/new.img")
    tab.controller.accept_inspection(generation, object(), "")
    assert tab.controller.inventory is None
    assert tab.get_config()["allocation_state"] == "pending"
    assert tab.inspection_pending()
    tab.shutdown_workers()
    tab.deleteLater()
    app.processEvents()
