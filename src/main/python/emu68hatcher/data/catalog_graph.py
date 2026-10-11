import logging

from emu68hatcher.data.package_schema import Package

logger = logging.getLogger(__name__)


def validate_dependency_graph(packages: list[Package]) -> None:
    """check requires/recommends/conflicts reference known packages or provides-tokens."""
    # raise on bad refs so a yaml typo fails the build instead of under-resolving silently
    tokens = {p.name.lower() for p in packages}
    for p in packages:
        tokens.update(t.lower() for t in p.provides)

    errors: list[str] = []
    by_name = {p.name: p for p in packages}
    for p in packages:
        seen = {p.name}
        source = p
        while source.archive_package:
            name = source.archive_package
            if name in seen or name not in by_name:
                errors.append(f"{p.name}: invalid or cyclic archive source {name!r}")
                break
            seen.add(name)
            source = by_name[name]
        else:
            if p.archive_package and (p.download or not source.download):
                errors.append(f"{p.name}: archive source needs one download definition")
        for t in p.requires + p.recommends:
            if t.lower() not in tokens:
                errors.append(
                    f"{p.name}: requires/recommends unknown '{t}' (no package or provides)"
                )
        unknown_conflicts = [t for t in p.conflicts if t.lower() not in tokens]
        if unknown_conflicts:
            logger.warning(f"{p.name}: conflicts reference unknown token(s) {unknown_conflicts}")
        both = {t.lower() for t in p.requires} & {t.lower() for t in p.conflicts}
        if both:
            errors.append(f"{p.name}: both requires and conflicts {sorted(both)}")
        # a package's own name in its requires/conflicts is always an authoring mistake
        # (the provides+conflicts mutual-exclusion idiom uses a separate token, not the name)
        name = p.name.lower()
        if name in {t.lower() for t in p.requires}:
            errors.append(f"{p.name}: requires itself")
        if name in {t.lower() for t in p.conflicts}:
            errors.append(f"{p.name}: conflicts with itself")

    if errors:
        raise ValueError("invalid package dependency graph:\n  " + "\n  ".join(errors))
