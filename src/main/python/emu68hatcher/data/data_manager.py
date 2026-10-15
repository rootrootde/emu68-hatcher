"""data management"""

from functools import cache
from pathlib import Path

import yaml

from emu68hatcher.data.catalog import UniqueKeyLoader

_REFERENCE_DIR = Path(__file__).parent / "reference"


@cache
def load_yaml_data(name: str):
    """load a YAML data file from the data directory (result cached per name)"""
    path = _REFERENCE_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"reference data file not found: {path}")

    content = path.read_text(encoding="utf-8")
    return yaml.load(content, Loader=UniqueKeyLoader) or []
