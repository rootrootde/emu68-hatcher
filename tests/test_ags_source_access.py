from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from emu68hatcher.builder import ags_validation
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.host.hst_commands import HSTCommand
from emu68hatcher.builder.host.hst_runner import HSTRunner
from emu68hatcher.builder.pipeline import create_image, validate
from emu68hatcher.config.schema import NetworkStack, OutputType


@pytest.mark.parametrize(
    "success,stdout,accepted",
    [
        (True, "Partitions:\nsource partitions", True),
        (False, "Permission denied", False),
        (True, "[time ERR] Access denied", False),
        (True, "", False),
    ],
)
def test_source_probe_uses_build_elevation(monkeypatch, success, stdout, accepted):
    workflow = Mock()
    source = Path("/Users/test/Downloads/ags.img")
    plan = SimpleNamespace(inventory=SimpleNamespace(source_path=source))
    run = Mock(return_value=SimpleNamespace(success=success, stdout=stdout, error=None))
    monkeypatch.setattr(HSTRunner, "run_command", run)
    if accepted:
        ags_validation.check_elevated_source_access(workflow, plan)
    else:
        with pytest.raises(BuildError, match="elevated build process cannot read"):
            ags_validation.check_elevated_source_access(workflow, plan)
    command = run.call_args.args[0]
    assert command.command == HSTCommand.RDB_INFO
    assert command.args == [str(source)]
    assert run.call_args.kwargs["elevation"] is workflow.state.elevation


def test_no_elevation_needs_no_second_source_probe(monkeypatch):
    workflow = Mock()
    workflow.state.elevation = None
    run = Mock()
    monkeypatch.setattr(HSTRunner, "run_command", run)
    ags_validation.check_elevated_source_access(workflow, Mock())
    run.assert_not_called()


def test_validation_checks_source_after_acquiring_elevation(monkeypatch):
    workflow = Mock()
    workflow.config.network_stack = NetworkStack.AMITCP_NG
    workflow.config.rgb2rtg.enabled = False
    workflow.state.elevation = None
    token = object()
    plan = Mock()
    for name in (
        "_existing_asset_directories",
        "_resolve_media",
        "_check_icon_set_adf",
        "_check_optional_package_adfs",
    ):
        monkeypatch.setattr(validate, name, Mock())
    monkeypatch.setattr(validate, "_resolve_rom", Mock(return_value=(None, None)))
    monkeypatch.setattr(ags_validation, "validate_ags_import", Mock(return_value=plan))

    def acquire(workflow):
        workflow.state.elevation = token

    def denied(workflow, checked_plan):
        assert workflow.state.elevation is token
        assert checked_plan is plan
        raise BuildError("source denied")

    monkeypatch.setattr(validate, "validate_output_target", acquire)
    monkeypatch.setattr(ags_validation, "check_elevated_source_access", denied)
    with pytest.raises(BuildError, match="source denied"):
        validate.stage_validate(workflow)


@pytest.mark.parametrize("output_type", [OutputType.DEVICE, OutputType.IMG])
def test_denied_source_stops_before_target_initialization(monkeypatch, output_type):
    from emu68hatcher.builder import ags_blocks, ags_source

    workflow = Mock()
    workflow.config.output.type = output_type
    extracted = Mock()
    monkeypatch.setattr(ags_source, "validate_source_identity", Mock())
    monkeypatch.setattr(ags_blocks, "read_source_blocks", Mock())
    monkeypatch.setattr(ags_validation, "check_source_destination", Mock())
    monkeypatch.setattr(
        ags_validation,
        "check_elevated_source_access",
        Mock(side_effect=BuildError("source denied")),
    )
    device = Mock()
    sparse = Mock()
    run = Mock()
    monkeypatch.setattr(create_image, "_prepare_device_target", device)
    monkeypatch.setattr(create_image, "_prepare_sparse_image", sparse)
    monkeypatch.setattr(HSTRunner, "run_command", run)
    monkeypatch.setattr(HSTRunner, "run_script", run)
    with pytest.raises(BuildError, match="source denied"):
        create_image.stage_create_image(workflow, extracted)
    device.assert_not_called()
    sparse.assert_not_called()
    run.assert_not_called()
