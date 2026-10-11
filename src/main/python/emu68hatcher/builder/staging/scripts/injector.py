"""inject add/before/after/remove edits into existing Amiga startup scripts (preserves original AmigaOS init)"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from emu68hatcher.builder.staging.files import resolve_staging_path

logger = logging.getLogger(__name__)


class InjectionAction(Enum):
    """type of script modification"""

    ADD = "Add"
    INJECT_BEFORE = "InjectBefore"
    INJECT_AFTER = "InjectAfter"
    REPLACE = "Replace"
    REMOVE = "Remove"


@dataclass
class ScriptInjection:
    """definition of a script injection"""

    target_script: str  # relative path like "S/Startup-Sequence"
    action: InjectionAction
    content_file: str | None = None  # path to content to inject
    content: str | None = None  # or inline content
    start_pattern: str | None = None  # regex for injection point
    end_pattern: str | None = None  # for Remove action
    name: str = ""  # comment marker name


@dataclass(frozen=True)
class InjectionResult:
    matched: bool
    changed: bool
    error: str | None = None


@dataclass(frozen=True)
class _EditResult:
    lines: list[str]
    matched: bool
    error: str | None = None


def read_amiga_script(path: Path) -> list[str]:
    """read an Amiga script file (ISO-8859-1 decodes every byte, no fallback needed)"""
    with open(path, encoding="iso-8859-1") as f:
        return f.read().splitlines()


def write_amiga_script(path: Path, lines: list[str]) -> None:
    """Write an Amiga script with LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with open(temp_path, "w", encoding="iso-8859-1", newline="\n") as f:
        for line in lines:
            f.write(line + "\n")
    temp_path.replace(path)


def inject_script(
    target_path: Path,
    injection: ScriptInjection,
    content_base_path: Path | None = None,
) -> InjectionResult:
    """apply one script edit"""
    if not target_path.exists():
        logger.info(f"Creating new script: {target_path}")
        original_lines = []
    else:
        original_lines = read_amiga_script(target_path)

    if injection.content:
        content_lines = injection.content.splitlines()
    elif injection.content_file and content_base_path:
        content_path = content_base_path / injection.content_file
        if not content_path.exists():
            error = f"Content file not found: {content_path}"
            logger.error(error)
            return InjectionResult(False, False, error)
        content_lines = read_amiga_script(content_path)
    else:
        content_lines = []

    injection_block = _build_injection_block(content_lines, injection.name)
    marker_error = _marker_state_error(original_lines, injection)
    if marker_error:
        logger.error(marker_error)
        return InjectionResult(False, False, marker_error)
    if _already_applied(original_lines, injection):
        return InjectionResult(True, False)

    try:
        if injection.action == InjectionAction.ADD:
            edit = _action_add(original_lines, injection_block)
        elif injection.action == InjectionAction.INJECT_BEFORE:
            edit = _action_inject_before(original_lines, injection_block, injection.start_pattern)
        elif injection.action == InjectionAction.INJECT_AFTER:
            edit = _action_inject_after(original_lines, injection_block, injection.start_pattern)
        elif injection.action == InjectionAction.REPLACE:
            edit = _action_replace(original_lines, injection_block, injection.start_pattern)
        elif injection.action == InjectionAction.REMOVE:
            edit = _action_remove(
                original_lines, injection.start_pattern, injection.end_pattern, injection.name
            )
        else:
            edit = _EditResult(
                original_lines,
                False,
                f"Unknown injection action: {injection.action}",
            )
    except re.error as e:
        edit = _EditResult(original_lines, False, f"Invalid injection pattern: {e}")

    if edit.error:
        logger.error(edit.error)
        return InjectionResult(edit.matched, False, edit.error)

    changed = edit.lines != original_lines
    if changed:
        write_amiga_script(target_path, edit.lines)
        logger.info(f"Applied injection '{injection.name}' to {target_path}")
    return InjectionResult(edit.matched, changed)


def _marker_lines(injection: ScriptInjection) -> tuple[str, str] | None:
    if not injection.name or injection.action == InjectionAction.REMOVE:
        return None
    return (
        f";{injection.name} - Added by Emu68 Hatcher - BEGIN",
        f";{injection.name} - Added by Emu68 Hatcher - END",
    )


def _marker_state_error(original: list[str], injection: ScriptInjection) -> str | None:
    markers = _marker_lines(injection)
    if markers is None:
        return None
    begin, end = markers
    if (begin in original) != (end in original):
        return f"Incomplete existing marker block for {injection.name}"
    return None


def _already_applied(original: list[str], injection: ScriptInjection) -> bool:
    markers = _marker_lines(injection)
    if markers is not None:
        return markers[0] in original and markers[1] in original
    if injection.action == InjectionAction.REMOVE and injection.name:
        marker = f";{injection.name} - Section Removed by Emu68 Hatcher"
        return marker in original
    return False


def _build_injection_block(content_lines: list[str], name: str) -> list[str]:
    """build an injection block with comment markers"""
    block = []

    if name:
        block.append("")
        block.append(f";{name} - Added by Emu68 Hatcher - BEGIN")
        block.append("")

    block.extend(content_lines)

    if name:
        block.append("")
        block.append(f";{name} - Added by Emu68 Hatcher - END")
        block.append("")

    return block


def _action_add(original: list[str], block: list[str]) -> _EditResult:
    """append block to end of script"""
    return _EditResult(original + block, True)


def _action_inject_before(
    original: list[str], block: list[str], pattern: str | None
) -> _EditResult:
    """insert block before first line matching pattern"""
    if not pattern:
        return _EditResult(original, False, "InjectBefore requires start_pattern")
    regex = re.compile(pattern, re.IGNORECASE)
    index = next((i for i, line in enumerate(original) if regex.search(line)), None)
    if index is None:
        return _EditResult(original, False, f"Required pattern not found: {pattern}")
    return _EditResult(original[:index] + block + original[index:], True)


def _action_inject_after(original: list[str], block: list[str], pattern: str | None) -> _EditResult:
    """insert block after first line matching pattern"""
    if not pattern:
        return _EditResult(original, False, "InjectAfter requires start_pattern")
    regex = re.compile(pattern, re.IGNORECASE)
    index = next((i for i, line in enumerate(original) if regex.search(line)), None)
    if index is None:
        return _EditResult(original, False, f"Required pattern not found: {pattern}")
    insert_at = index + 1
    return _EditResult(original[:insert_at] + block + original[insert_at:], True)


def _action_replace(original: list[str], block: list[str], pattern: str | None) -> _EditResult:
    """Replace the first matching command without moving it."""
    if not pattern:
        return _EditResult(original, False, "Replace requires start_pattern")
    regex = re.compile(pattern, re.IGNORECASE)
    index = next((i for i, line in enumerate(original) if regex.search(line)), None)
    if index is None:
        return _EditResult(original, False, f"Required pattern not found: {pattern}")
    return _EditResult(original[:index] + block + original[index + 1 :], True)


def _action_remove(
    original: list[str], start_pattern: str | None, end_pattern: str | None, name: str
) -> _EditResult:
    """remove lines between start and end patterns"""
    if not start_pattern or not end_pattern:
        return _EditResult(original, False, "Remove requires both start_pattern and end_pattern")
    start_regex = re.compile(start_pattern, re.IGNORECASE)
    end_regex = re.compile(end_pattern, re.IGNORECASE)
    start = next((i for i, line in enumerate(original) if start_regex.search(line)), None)
    if start is None:
        return _EditResult(original, False)
    end = next(
        (i for i in range(start, len(original)) if end_regex.search(original[i])),
        None,
    )
    if end is None:
        return _EditResult(original, True, f"Remove end pattern not found: {end_pattern}")
    marker = ["", f";{name} - Section Removed by Emu68 Hatcher", ""]
    return _EditResult(original[:start] + marker + original[end + 1 :], True)


# InjectAfter BindDrivers stacks LIFO; list reverse of exec order: UAEGFX, FirstBoot, REXXMAST, RTC
STARTUP_SEQUENCE_INJECTIONS = [
    # ROM CheckInstall block is kept intact (matches the reference imager). its
    # LoadModule steps soft-kick the patched graphics/intuition modules from L:
    # when the ROM is older than the installed update; for a current 3.2.3 ROM the
    # version checks skip LoadModule and just run SetPatch. the L: update files are
    # installed, so the old "no L: files -> QUIT" failure no longer applies.
    # remove CPU CHECKINSTALL section - not needed for Emu68 (causes error on 68040)
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.REMOVE,
        start_pattern=r";-+\s*CPU CheckInstall",
        end_pattern=r";-+\s*End of CPU CheckInstall",
        name="CPU CheckInstall (not needed for Emu68)",
    ),
    # remove the original RexxMast - moved to after BindDrivers so FirstBoot scripts can use ARexx
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.REMOVE,
        start_pattern=r"If EXISTS SYS:System/RexxMast",
        end_pattern=r"EndIf",
        name="Original RexxMast (moved to after BindDrivers)",
    ),
    # the 3.9 CD Startup-Sequence starts RexxMast with a bare line (no If EXISTS
    # wrapper); remove that too, else our post-BindDrivers RexxMast double-starts it
    # and the second start fails rc20
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.REMOVE,
        start_pattern=r"^SYS:System/RexxMast",
        end_pattern=r"^SYS:System/RexxMast",
        name="Original RexxMast bare line (moved to after BindDrivers)",
    ),
    # Older SetPatch versions need RemLib before loading the replacement icon.library.
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.INJECT_BEFORE,
        content_file="S/Startup-Sequence_Iconlib",
        # Cloanto's conditional SetPatch calls must keep their Version return codes.
        start_pattern=(
            r"^(?:C:)?(?:SetPatch(?:\s|$)|"
            r"Version\s+>NIL:\s+exec\.library\s+45\s+20(?:\s|$))"
        ),
        name="Iconlib",
    ),
    # UAEGFX persistent monitor swap (runs 5th, furthest from anchor)
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.INJECT_AFTER,
        content_file="S/Startup-Sequence_UAEGFX",
        start_pattern=r"BindDrivers",
        name="UAEGFX Monitor Swap",
    ),
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.INJECT_BEFORE,
        content_file="S/Startup-Sequence_UAE_ScreenMode",
        start_pattern=r"^(?:C:)?IPrefs(?:\s|$)",
        name="UAE ScreenMode",
    ),
    # main FirstBoot section (runs 4th)
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.INJECT_AFTER,
        content_file="S/Startup-Sequence_FirstBoot",
        start_pattern=r"BindDrivers",
        name="FirstBoot Section",
    ),
    # RexxMast - start ARexx interpreter (runs 2nd)
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.INJECT_AFTER,
        content_file="S/Startup-Sequence_REXXMAST",
        start_pattern=r"BindDrivers",
        name="RexxMast",
    ),
    # RTC load - I2C or clockport, auto-probed (runs 1st: before RexxMast's Wait 5)
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.INJECT_AFTER,
        content_file="S/Startup-Sequence_RTC",
        start_pattern=r"BindDrivers",
        name="RTC Load",
    ),
    # second+ boots: BindDrivers loads SD0 before the Mount glob, suppress the duplicate-mount error
    ScriptInjection(
        target_script="S/Startup-Sequence",
        action=InjectionAction.REPLACE,
        content="Mount >NIL: DEVS:DOSDrivers/~(#?.info)",
        start_pattern=r"^\s*(?:C:)?Mount\s+(?:>NIL:\s+)?DEVS:DOSDrivers(?:/|\s|$)",
        name="Mount DOSDrivers (with >NIL:)",
    ),
]


def apply_standard_injections(
    staging_dir: Path,
    content_base_path: Path,
    *,
    use_remlib: bool = True,
) -> list[InjectionResult]:
    """apply the Startup-Sequence surgery injections to staged files"""
    results = []
    for injection in STARTUP_SEQUENCE_INJECTIONS:
        # OS 3.9 SetPatch replaces icon.library itself; leave the resident list intact.
        if injection.name == "Iconlib" and not use_remlib:
            continue
        target = staging_dir / injection.target_script
        results.append(inject_script(target, injection, content_base_path))
    return results


def apply_package_scripts(
    staging_dir: Path,
    package_names: list[str],
    user_archives: set[str],
) -> int:
    """append installed packages' schema 'scripts:' blocks to their target scripts"""
    from emu68hatcher.data.package_loader import get_package_by_name

    count = 0
    for pkg_name in package_names:
        pkg = get_package_by_name(pkg_name)
        if not pkg or not pkg.scripts:
            continue
        for mod in pkg.scripts:
            # cached catalogs still register fonts here; first boot now does it
            if pkg.name.casefold() == "ttflib" and mod.name == "TrueType fonts":
                continue
            if mod.when_user_archive is not None and mod.when_user_archive != (
                pkg_name in user_archives
            ):
                continue
            injection = ScriptInjection(
                target_script=mod.target,
                action=InjectionAction.ADD,
                content=mod.content,
                name=mod.name,
            )
            result = inject_script(resolve_staging_path(staging_dir, mod.target), injection)
            if result.error:
                logger.warning("Package script %s failed: %s", mod.name, result.error)
            if result.changed:
                count += 1
    return count
