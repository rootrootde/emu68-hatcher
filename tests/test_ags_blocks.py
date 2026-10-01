from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from emu68hatcher.builder.ags_blocks import verify_copy_result
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.config.ags_models import AGSImportConfig
from emu68hatcher.config.loader import migrate_config_data
from emu68hatcher.config.schema import BuildConfig


@pytest.mark.parametrize(
    "line,done",
    [
        ("12.3% [4 MB/s] [8 MB / 64 MB] [00:02 / 00:16]", 123),
        ("12,3% [4 MB/s] [8 MB / 64 MB] [00:02 / 00:16]", 123),
        ("100% [4 MB/s] [64 MB / 64 MB] [00:16 / 00:16]", 1000),
        ("Copying 250 / 1000", 250),
        ("Copying 250 / 2000", None),
        ("101% [4 MB/s] [64 MB / 64 MB] [00:16 / 00:16]", None),
        ("copied 12.3%", None),
    ],
)
def test_partition_copy_reports_live_progress(monkeypatch, line, done):
    from emu68hatcher.builder import ags_blocks, ags_source
    from emu68hatcher.builder.host.hst_commands import HSTCommand, HSTCommandLine, HSTScript

    monkeypatch.setattr(ags_source, "validate_source_identity", lambda inventory: None)
    monkeypatch.setattr(ags_blocks, "verify_target_partitions", lambda *args: None)
    partition = SimpleNamespace(index=1, size=1000)
    plan = SimpleNamespace(
        inventory=SimpleNamespace(
            partitions=[partition], components=[SimpleNamespace(partition=partition)]
        )
    )
    command = HSTCommandLine(HSTCommand.RDB_PART_COPY, ["source", "1", "target"], "Copy WHD")
    workflow = Mock()

    def run_command(command, **kwargs):
        workflow._update_state.reset_mock()
        kwargs["on_line"]("stdout", line)
        if done is None:
            workflow._update_state.assert_not_called()
        else:
            workflow._update_state.assert_called_once_with(
                progress=95 * done / 1000, message=f"Copy WHD: {done} / 1000 B"
            )
        return SimpleNamespace(
            success=True,
            stdout="Destination partition number '1':\n- Size '1 KB' (1000 bytes)\n"
            "Copied '1 KB' (1000 bytes) in 0h:00m:00s\n",
        )

    runner = SimpleNamespace(run_command=run_command)
    ags_blocks.copy_creation_script(workflow, runner, HSTScript([command]), plan, "target")


@pytest.mark.parametrize(
    "size,initial,halfway",
    [
        (512, "0 / 512 B", "256 / 512 B"),
        (4096, "0.00 / 4.00 KiB", "2.00 / 4.00 KiB"),
        (4 * 1024**2, "0.00 / 4.00 MiB", "2.00 / 4.00 MiB"),
        (10_737_893_376, "0.00 / 10.00 GiB", "5.00 / 10.00 GiB"),
    ],
)
def test_partition_copy_status_uses_readable_units(monkeypatch, size, initial, halfway):
    from emu68hatcher.builder import ags_blocks, ags_source
    from emu68hatcher.builder.host.hst_commands import HSTCommand, HSTCommandLine, HSTScript

    monkeypatch.setattr(ags_source, "validate_source_identity", lambda inventory: None)
    monkeypatch.setattr(ags_blocks, "verify_target_partitions", lambda *args: None)
    partition = SimpleNamespace(index=1, size=size)
    plan = SimpleNamespace(
        inventory=SimpleNamespace(
            partitions=[partition], components=[SimpleNamespace(partition=partition)]
        )
    )
    command = HSTCommandLine(
        HSTCommand.RDB_PART_COPY, ["source", "1", "target"], "Copy AGS Games to SDH2"
    )
    workflow = Mock()

    def run_command(command, **kwargs):
        workflow._update_state.assert_called_once_with(
            progress=0, message=f"Copy AGS Games to SDH2: {initial}"
        )
        kwargs["on_line"]("stdout", f"Copying {size // 2} / {size}")
        assert workflow._update_state.call_args.kwargs == {
            "progress": 47.5,
            "message": f"Copy AGS Games to SDH2: {halfway}",
        }
        return SimpleNamespace(
            success=True,
            stdout=f"Destination partition number '1':\n- Size 'size' ({size} bytes)\n"
            f"Copied 'size' ({size} bytes) in 0h:00m:00s\n",
        )

    runner = SimpleNamespace(run_command=run_command)
    ags_blocks.copy_creation_script(workflow, runner, HSTScript([command]), plan, "target")


@pytest.mark.parametrize("copied,size", [(0, 2048), (1024, 2048), (2048, 1024)])
def test_zero_exit_does_not_accept_incomplete_partition(copied, size):
    result = SimpleNamespace(
        success=True,
        stdout=(
            f"Destination partition number '2':\n- Size '2 KB' ({size} bytes)\n"
            f"Copied '2 KB' ({copied} bytes) in 0h:00m:00s\n"
        ),
    )
    with pytest.raises(BuildError):
        verify_copy_result(result, 2048)


def test_old_ready_selection_requires_new_preview():
    config = BuildConfig().model_dump(mode="json")
    config["version"] = "1.2.0"
    config["ags_import"] = {
        "source_image": "/tmp/ags.img",
        "allocation_state": "ready",
        "components": {"whdload": True, "games": True, "emulators": True, "media": False},
    }
    migrated = BuildConfig.model_validate(migrate_config_data(config))
    assert isinstance(migrated.ags_import, AGSImportConfig)
    assert migrated.ags_import.allocation_state == "pending"
    assert migrated.ags_import.components.work
    assert "applications" in migrated.ags_import.migration_notice


def test_copy_commands_keep_windows_device_path_and_partition_order():
    from emu68hatcher.builder.host.hst_commands import HSTCommand, generate_disk_creation_script
    from emu68hatcher.config.ags_models import AGSPartitionReservation
    from emu68hatcher.config.partition_helpers import create_default_partition_layout
    from emu68hatcher.config.partition_models import AmigaPartition

    config = BuildConfig(partitions=create_default_partition_layout(32))
    reserved = AmigaPartition(
        device="SDH4",
        volume="WHDLoad",
        size=2048 * 516096,
        ags_reservation=AGSPartitionReservation(role="whdload", minimum_size=2048 * 516096),
    )
    config.partitions.layout[1].amiga_partitions += [
        reserved,
        AmigaPartition(device="SDH2", volume="Data", size=1024 * 516096),
    ]
    source = SimpleNamespace(index=3, size=reserved.size, volume="WHDLoad")
    plan = SimpleNamespace(
        inventory=SimpleNamespace(
            source_path="/tmp/ags.img", component=lambda role: SimpleNamespace(partition=source)
        )
    )
    raw = r"\\.\PhysicalDrive7"
    script = generate_disk_creation_script(config, raw, skip_blank=True, ags_plan=plan)
    commands = [
        c
        for c in script.commands
        if c.command
        in {HSTCommand.RDB_PART_ADD, HSTCommand.RDB_PART_COPY, HSTCommand.RDB_PART_FORMAT}
    ]
    assert [c.command for c in commands] == [
        HSTCommand.RDB_PART_ADD,
        HSTCommand.RDB_PART_FORMAT,
        HSTCommand.RDB_PART_COPY,
        HSTCommand.RDB_PART_ADD,
        HSTCommand.RDB_PART_FORMAT,
    ]
    assert commands[2].args[2] == raw + r"\mbr\2"
    assert commands[2].description == "Copy AGS WHDLoad to SDH4"
    assert commands[-1].args[1] == "3"
