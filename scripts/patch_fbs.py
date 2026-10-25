"""patch the free fbs 1.2.7 from pypi so it freezes this project"""

import re
import sys
from pathlib import Path

import fbs
import fbs.freeze
import fbs.freeze.mac
import fbs.installer.mac

# (file, pattern, replacement): the free build refuses python other than 3.5/3.6, its runtime
# hook would put fbs_runtime into the app, its macos freeze moves
# Contents/MacOS/base_library.zip, which pyinstaller 6 no longer puts there, and its bundled
# create-dmg runs bless --openfolder, which fails on apple silicon (create-dmg from PATH instead)
PATCHES = [
    (fbs.__file__, r"^(\s+)if sys\.version_info\[0\] != 3 or .*?\n(?:\1 .*\n)+", ""),
    (fbs.freeze.__file__, r"^\s+args\.extend\(\['--runtime-hook', hook_path\]\)\n", ""),
    (fbs.freeze.mac.__file__, r"^(\s+)_fix_sparkle_delta_updates\(\)\s*$", r"\1pass"),
    (
        fbs.installer.mac.__file__,
        r"join\(dirname\(__file__\), 'create-dmg', 'create-dmg'\)",
        "'create-dmg'",
    ),
]

for file, pattern, replacement in PATCHES:
    path = Path(file)
    src, count = re.subn(pattern, replacement, path.read_text(), flags=re.M)
    if count != 1:
        sys.exit(f"patch_fbs: pattern not found in {path} (fbs version changed?)")
    path.write_text(src)
    print(f"patched {path}")
