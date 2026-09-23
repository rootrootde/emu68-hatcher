import json
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from emu68hatcher.builder.ags_validation import _macos_source_devices
from emu68hatcher.builder.errors import BuildError
from emu68hatcher.builder.host._disk_linux import list_disks


def test_apfs_source_uses_physical_store_not_synthesized_disk():
    info = {
        "FilesystemType": "apfs",
        "ParentWholeDisk": "disk5",
        "APFSPhysicalStores": [{"APFSPhysicalStore": "disk4s2"}],
    }
    result = SimpleNamespace(returncode=0, stdout=plistlib.dumps(info))
    with patch("emu68hatcher.builder.ags_validation.subprocess.run", return_value=result):
        assert _macos_source_devices(Path("/Volumes/External/ags.img")) == {"/dev/disk4"}


def test_apfs_source_without_physical_store_is_rejected():
    info = {"FilesystemType": "apfs", "ParentWholeDisk": "disk5"}
    result = SimpleNamespace(returncode=0, stdout=plistlib.dumps(info))
    with patch("emu68hatcher.builder.ags_validation.subprocess.run", return_value=result):
        with pytest.raises(BuildError, match="Cannot identify the disk"):
            _macos_source_devices(Path("/Volumes/External/ags.img"))


@pytest.mark.parametrize("mount", ["/media/ags", "/"])
def test_linux_disk_includes_nested_mapper_mount(mount):
    device = {
        "name": "sdb",
        "type": "disk",
        "rm": True,
        "ro": False,
        "size": 64000000000,
        "children": [{"name": "sdb1", "children": [{"name": "encrypted", "mountpoints": [mount]}]}],
    }
    result = SimpleNamespace(returncode=0, stdout=json.dumps({"blockdevices": [device]}))
    with patch("emu68hatcher.builder.host._disk_linux.subprocess.run", return_value=result):
        (disk,) = list_disks()
    assert disk.mounted_partitions == [mount]
    assert disk.is_system_disk == (mount == "/")
