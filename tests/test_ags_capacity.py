from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from emu68hatcher.builder.ags_validation import check_host_capacity
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.schema import OutputConfig

GIB = 1024**3


@pytest.mark.parametrize(
    ("sparse", "free_gib", "passes"),
    [(True, 38, True), (True, 32, False), (False, 38, False), (False, 67, True)],
)
def test_capacity_excludes_empty_space_inside_normal_partitions(
    tmp_path, monkeypatch, sparse, free_gib, passes
):
    normal = SimpleNamespace(size=4 * GIB, ags_reservation=None)
    copied = SimpleNamespace(size=30 * GIB, ags_reservation=object())
    empty = SimpleNamespace(size=24 * GIB, ags_reservation=None)
    partitions = SimpleNamespace(
        disk_size=64 * GIB,
        iter_amiga_partitions=lambda: iter((normal, copied, empty)),
        layout=[
            SimpleNamespace(type="fat32", size=GIB),
            SimpleNamespace(
                type="id76",
                size=62 * GIB,
                amiga_partitions=[normal, copied, empty],
            ),
        ],
    )
    workflow = SimpleNamespace(
        logger=Mock(),
        config=SimpleNamespace(
            output=OutputConfig(path=tmp_path / "target.img", sparse=sparse),
            partitions=partitions,
        ),
    )
    plan = SimpleNamespace(
        requirements=[
            SimpleNamespace(estimated_host_bytes=16 * 1024**2, minimum_partition_bytes=30 * GIB)
        ]
    )
    monkeypatch.setattr(
        "emu68hatcher.builder.ags_validation._workspace_filesystem", lambda: tmp_path
    )
    monkeypatch.setattr(
        "emu68hatcher.builder.ags_validation.shutil.disk_usage",
        lambda path: SimpleNamespace(free=free_gib * GIB),
    )
    if passes:
        check_host_capacity(workflow, plan)
    else:
        with pytest.raises(BuildError, match="free"):
            check_host_capacity(workflow, plan)


@pytest.mark.parametrize("extras_gib", [0, 2])
def test_full_ags_layout_fits_sparse_but_counts_extra_content_twice(
    tmp_path, monkeypatch, extras_gib
):
    from emu68hatcher.config.partition_helpers import build_partition_config
    from emu68hatcher.config.schema import AmigaPartition

    roles = ("whdload", "games", "work", "media")
    copied_sizes = (11811373056, 10737893376, 2147991552, 4842012672)
    normal_sizes = (4257275904, 8429912064, 20561780736)
    normal = [
        AmigaPartition(
            device=f"SDH{index + 4}",
            volume=f"Normal{index}",
            size=size,
            bootable=index == 0,
        )
        for index, size in enumerate(normal_sizes)
    ]
    copied = [
        AmigaPartition(
            device=f"SDH{index}",
            volume=role,
            size=size,
            ags_reservation={"role": role, "minimum_size": size},
        )
        for index, (role, size) in enumerate(zip(roles, copied_sizes, strict=True))
    ]
    partitions = build_partition_config(63864569856, GIB, [normal[0], *copied, *normal[1:]])
    workflow = SimpleNamespace(
        logger=Mock(),
        config=SimpleNamespace(
            output=OutputConfig(path=tmp_path / "target.img"), partitions=partitions
        ),
    )
    plan = SimpleNamespace(
        requirements=[
            SimpleNamespace(estimated_host_bytes=16 * 1024**2, minimum_partition_bytes=size)
            for size in copied_sizes
        ]
    )
    monkeypatch.setattr(
        "emu68hatcher.builder.ags_validation._workspace_filesystem", lambda: tmp_path
    )
    monkeypatch.setattr(
        "emu68hatcher.builder.ags_validation.shutil.disk_usage",
        lambda path: SimpleNamespace(free=int(34.4 * GIB)),
    )
    if extras_gib:
        with pytest.raises(BuildError, match="free"):
            check_host_capacity(workflow, plan, extras_gib * GIB)
    else:
        check_host_capacity(workflow, plan)
        workflow.config.output.sparse = False
        with pytest.raises(BuildError, match="61.5 GiB"):
            check_host_capacity(workflow, plan)
