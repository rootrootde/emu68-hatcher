from emu68hatcher.config.ags_layout import plan_ags_layout
from emu68hatcher.config.constants import CYLINDER_SIZE
from emu68hatcher.config.partition_helpers import create_default_partition_layout
from emu68hatcher.config.partition_models import AmigaPartition


def test_existing_work_is_not_removed_or_resized(tmp_path):
    layout = create_default_partition_layout(64)
    work = AmigaPartition(
        device="SDH1", volume="Work", size=4096 * CYLINDER_SIZE, extra_content_directory=tmp_path
    )
    layout.layout[1].amiga_partitions.append(work)
    before = layout.model_dump()
    proposal = plan_ags_layout(
        layout, {"whdload": work.size, "work": work.size}, ("whdload", "work")
    )
    assert proposal.errors
    assert not proposal.changes
    assert layout.model_dump() == before
    assert proposal.partitions[1].extra_content_directory == tmp_path


def test_manual_partition_is_not_shrunk_to_fit_import():
    layout = create_default_partition_layout(8)
    original = layout.model_dump()
    proposal = plan_ags_layout(layout, {"whdload": 20000 * CYLINDER_SIZE}, ("whdload",))
    assert proposal.errors
    assert not proposal.changes
    assert layout.model_dump() == original
