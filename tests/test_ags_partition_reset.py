from emu68hatcher.config.ags_layout import apply_ags_layout, plan_ags_layout
from emu68hatcher.config.constants import CYLINDER_SIZE
from emu68hatcher.gui.partition_editor_model import PartitionEditorModel


def _aligned_size(gib):
    size = gib * 1024**3
    return ((size + CYLINDER_SIZE - 1) // CYLINDER_SIZE) * CYLINDER_SIZE


def test_reset_keeps_fixed_reservations():
    model = PartitionEditorModel(256)
    original = model.to_config()
    roles = ("whdload", "games", "work")
    requirements = {role: _aligned_size(30) for role in roles}
    proposal = plan_ags_layout(original, requirements, roles)
    assert not proposal.errors
    model.load(apply_ags_layout(original, proposal))
    reservations_before = {
        part.ags_reservation.role: (part.device, part.size)
        for part in model.partitions
        if part.ags_reservation
    }

    model.reset(disk_size_bytes=model.disk_size)

    assert not model.errors
    assert model.free_space >= 0
    assert {
        part.ags_reservation.role: (part.device, part.size)
        for part in model.partitions
        if part.ags_reservation
    } == reservations_before
    assert len(model.partitions) == 4
    model.to_config()


def test_pfs3_size_limit_remains_cylinder_aligned():
    from emu68hatcher.config.constants import PFS3_MAX_PARTITION_SIZE

    model = PartitionEditorModel(256)
    model.set_partition_size_mb(0, 110 * 1024)
    size = model.partitions[0].size
    assert size == PFS3_MAX_PARTITION_SIZE // CYLINDER_SIZE * CYLINDER_SIZE
    assert not model.errors
    model.to_config()


def test_invalid_reserved_reset_cannot_be_applied(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from emu68hatcher.gui.tabs.partitions import PartitionsTab
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    tab = PartitionsTab()
    roles = ("whdload", "games", "work")
    original = tab.get_config()
    proposal = plan_ags_layout(original, {role: _aligned_size(1) for role in roles}, roles)
    assert not proposal.errors
    tab.set_config(apply_ags_layout(original, proposal))
    tab._model.change_disk_size(2 * 1024**3)
    previous = [(part.device, part.size) for part in tab._model.partitions]
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: (_ for _ in ()).throw(AssertionError("invalid reset offered Apply")),
    )

    tab._reset_to_default()

    assert warnings
    assert "Cannot apply" in warnings[0][2]
    assert [(part.device, part.size) for part in tab._model.partitions] == previous
    tab.deleteLater()
    app.processEvents()
