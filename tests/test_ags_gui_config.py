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
    from emu68hatcher.gui.partition_editor_model import PartitionEditorModel
    from emu68hatcher.gui.tabs import ags_tab
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    model = PartitionEditorModel(64)
    original = model.to_config()
    proposal = plan_ags_layout(original, {"whdload": round_to_cylinder(16 * 1024**3)}, ("whdload",))
    model.load(apply_ags_layout(original, proposal))
    tab = ags_tab.AGSTab()
    tab.set_partitions(model.to_config())
    tab.enabled_check.setChecked(True)
    applied = []
    tab.layout_applied.connect(applied.append)
    revision = tab._revision
    model.change_disk_size(4 * 1024**3)
    assert model.errors
    tab.set_layout_error("too small")
    assert tab._partitions is None
    assert tab._revision > revision

    def unexpected_dialog(*args):
        raise AssertionError("proposal opened for invalid layout")

    monkeypatch.setattr(ags_tab, "AGSProposalDialog", unexpected_dialog)
    tab.preview_partitions()
    assert not tab._show_proposal(())
    tab.enabled_check.setChecked(False)
    assert tab.enabled_check.isChecked()
    tab._update_buttons()
    assert not tab.preview_btn.isEnabled()
    assert not applied
    assert model.disk_size == 4 * 1024**3

    model.change_disk_size(64 * 1024**3)
    tab.set_partitions(model.to_config())
    assert tab._has_reservations()
    tab.deleteLater()
    app.processEvents()


def test_open_proposal_is_rejected_after_layout_becomes_invalid(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.config.partition_helpers import create_default_partition_layout
    from emu68hatcher.gui.tabs import ags_tab
    from PySide6.QtWidgets import QApplication, QDialog

    app = QApplication.instance() or QApplication([])
    tab = ags_tab.AGSTab()
    tab.set_partitions(create_default_partition_layout(64))
    applied = []
    tab.layout_applied.connect(applied.append)

    def accept_after_edit(dialog):
        tab.set_layout_error("too small")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ags_tab.AGSProposalDialog, "exec", accept_after_edit)
    assert not tab._show_proposal(())
    assert not applied
    assert tab._partitions is None
    tab.deleteLater()
    app.processEvents()
