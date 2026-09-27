def test_disabled_empty_ags_does_not_block_normal_config(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.tabs.ags_tab import AGSTab
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    tab = AGSTab()
    from emu68hatcher.config.partition_helpers import create_default_partition_layout

    tab.set_partitions(create_default_partition_layout(64))
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
    tab = AGSTab()
    original = editor.get_config()
    proposal = plan_ags_layout(original, {"whdload": round_to_cylinder(16 * 1024**3)}, ("whdload",))
    editor.set_config(apply_ags_layout(original, proposal))
    tab.enabled_check.setChecked(True)
    tab.set_partitions(editor.get_config())
    editor.layout_changed.connect(lambda: tab.set_partitions(editor.get_layout_draft()))
    tab.layout_applied.connect(editor.set_config)
    editor._model.change_disk_size(4 * 1024**3)
    editor._refresh_table()
    assert tab._partitions.disk_size == 4 * 1024**3
    assert editor._model.errors

    tab.enabled_check.setChecked(False)

    assert editor._model.disk_size == 4 * 1024**3
    assert not tab._has_reservations()
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
    tab.set_partitions(create_default_partition_layout(64))
    tab.enabled_check.setChecked(True)
    tab.source_edit.setText("/old.img")
    generation = tab._generation
    tab.source_edit.setText("/new.img")
    tab._accept_inspection(generation, object(), "")
    assert tab._inventory is None
    assert tab.get_config()["allocation_state"] == "pending"
    assert tab.inspection_pending()
    tab.shutdown_workers()
    tab.deleteLater()
    app.processEvents()
