"""dependency resolution for amiga packages: requires/recommends/conflicts/provides"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from emu68hatcher.data.package_loader import get_packages_for_version
from emu68hatcher.data.package_schema import Package, _group_rank

logger = logging.getLogger(__name__)


@dataclass
class Resolution:
    """the outcome of resolving a requested package set."""

    selected: set[str] = field(default_factory=set)
    install_order: list[str] = field(default_factory=list)  # dep-before-dependent
    dropped: dict[str, str] = field(default_factory=dict)  # name -> reason (lost conflict / orphan)
    unsatisfiable: dict[str, list[str]] = field(default_factory=dict)  # token -> requirers
    required_by: dict[str, list[str]] = field(default_factory=dict)
    recommended_by: dict[str, list[str]] = field(default_factory=dict)
    selection_reasons: dict[str, str] = field(default_factory=dict)


def _provides_of(pkg: Package) -> set[str]:
    """tokens a package satisfies: its own name plus its declared provides."""
    return {pkg.name.lower(), *(t.lower() for t in pkg.provides)}


@dataclass(frozen=True)
class _ResolverContext:
    packages: list[Package]
    by_name: dict[str, Package]
    providers: dict[str, list[str]]
    mandatory: set[str]
    requested: set[str]
    disabled: set[str]

    @classmethod
    def create(
        cls,
        requested: set[str],
        disabled: set[str],
        kickstart_version: str,
        emu68_version: str | None,
        packages: list[Package] | None = None,
    ) -> _ResolverContext:
        if packages is None:
            packages = get_packages_for_version(kickstart_version, emu68_version)
        mandatory = {pkg.name for pkg in packages if pkg.mandatory}
        by_name = {pkg.name.lower(): pkg for pkg in packages}
        providers: dict[str, list[str]] = {}
        for pkg in packages:
            for token in _provides_of(pkg):
                providers.setdefault(token, []).append(pkg.name.lower())
        return cls(packages, by_name, providers, mandatory, requested, disabled)

    def pick_provider(
        self,
        token: str,
        selected: set[str],
        excluded: set[str],
    ) -> str | None:
        candidates = [name for name in self.providers.get(token, []) if name not in excluded]
        if not candidates:
            return None
        for name in candidates:
            if name in selected:
                return name
        preferences = (
            lambda name: name in self.requested,
            lambda name: name in self.mandatory,
            lambda name: self.by_name[name].default,
        )
        for preference in preferences:
            matches = [name for name in candidates if preference(name)]
            if matches:
                return sorted(matches)[0]
        return sorted(candidates)[0]

    def dependency_closure(
        self,
        excluded: set[str],
    ) -> tuple[set[str], dict[str, set[str]], dict[str, list[str]]]:
        selected: set[str] = set()
        requirers: dict[str, set[str]] = {}
        unsatisfiable: dict[str, list[str]] = {}
        work = list((self.requested | self.mandatory) - excluded)
        while work:
            name = work.pop()
            if name in selected or name in excluded or name not in self.by_name:
                continue
            selected.add(name)
            package = self.by_name[name]
            requirements = package.requires + (
                [package.archive_package] if package.archive_package else []
            )
            for requirement in requirements:
                token = requirement.lower()
                provider = self.pick_provider(token, selected, excluded)
                if provider is None:
                    unsatisfiable.setdefault(token, []).append(name)
                    continue
                requirers.setdefault(provider, set()).add(name)
                if provider not in selected:
                    work.append(provider)
            for recommendation in package.recommends:
                token = recommendation.lower()
                if token in self.disabled:
                    continue
                provider = self.pick_provider(token, selected, excluded | self.disabled)
                if provider and provider not in selected:
                    work.append(provider)
        return selected, requirers, unsatisfiable

    def conflict_components(self, selected: set[str]) -> list[set[str]]:
        adjacency: dict[str, set[str]] = {name: set() for name in selected}
        names = sorted(selected)
        provided = {name: _provides_of(self.by_name[name]) for name in selected}
        conflicts = {
            name: {token.lower() for token in self.by_name[name].conflicts} for name in selected
        }
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                if conflicts[left] & provided[right] or conflicts[right] & provided[left]:
                    adjacency[left].add(right)
                    adjacency[right].add(left)
        seen: set[str] = set()
        components: list[set[str]] = []
        for name in names:
            if name in seen or not adjacency[name]:
                continue
            stack = [name]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(adjacency[current] - component)
            seen |= component
            components.append(component)
        return components

    def conflict_losers(self, selected: set[str]) -> tuple[set[str], dict[str, str]]:
        excluded: set[str] = set()
        dropped: dict[str, str] = {}
        for component in self.conflict_components(selected):
            provided = {name: _provides_of(self.by_name[name]) for name in component}
            conflicts = {
                name: {token.lower() for token in self.by_name[name].conflicts}
                for name in component
            }
            mandatory = sorted(component & self.mandatory)
            bad_pair = next(
                (
                    (left, right)
                    for index, left in enumerate(mandatory)
                    for right in mandatory[index + 1 :]
                    if conflicts[left] & provided[right] or conflicts[right] & provided[left]
                ),
                None,
            )
            if bad_pair:
                raise ValueError(
                    f"mandatory packages {bad_pair[0]} and {bad_pair[1]} conflict and cannot "
                    "coexist (fix their provides/conflicts in the yaml)"
                )
            priority = sorted(
                component,
                key=lambda name: (
                    name not in self.mandatory,
                    name not in self.requested,
                    not self.by_name[name].default,
                    name,
                ),
            )
            kept: list[str] = []
            for name in priority:
                clash = next(
                    (
                        other
                        for other in kept
                        if conflicts[name] & provided[other] or conflicts[other] & provided[name]
                    ),
                    None,
                )
                if clash is None:
                    kept.append(name)
                else:
                    excluded.add(name)
                    dropped[name] = f"conflicts with {clash}"
        return excluded, dropped


def resolve(
    requested: set[str],
    deselected: set[str],
    kickstart_version: str,
    emu68_version: str | None = None,
    *,
    order_hint: list[str] | None = None,
    packages: list[Package] | None = None,
) -> Resolution:
    """resolve a user selection into a complete, conflict-free, ordered install set."""
    requested = {n.lower() for n in requested}
    deselected = {n.lower() for n in deselected}
    context = _ResolverContext.create(
        requested,
        deselected,
        kickstart_version,
        emu68_version,
        packages,
    )
    excluded: set[str] = set()
    dropped: dict[str, str] = {}
    selected: set[str] = set()
    requirers: dict[str, set[str]] = {}
    unsatisfiable: dict[str, list[str]] = {}

    for _ in range(len(context.packages) + 1):
        selected, requirers, unsatisfiable = context.dependency_closure(excluded)
        new_excluded, new_dropped = context.conflict_losers(selected)
        dropped.update(new_dropped)
        if not (new_excluded - excluded):
            break
        excluded |= new_excluded
    else:
        logger.warning("dependency resolver hit its iteration bound; selection may be incomplete")

    install_order = _topological_order(selected, context.by_name, requirers, order_hint)

    recommended_by: dict[str, list[str]] = {}
    for name in sorted(selected):
        for token in context.by_name[name].recommends:
            if token in deselected:
                continue
            provider = context.pick_provider(token, selected, excluded | deselected)
            if provider in selected:
                recommended_by.setdefault(provider, []).append(name)

    return Resolution(
        selected=selected,
        install_order=install_order,
        dropped=dropped,
        unsatisfiable={key: sorted(set(value)) for key, value in unsatisfiable.items()},
        required_by={key: sorted(value) for key, value in requirers.items()},
        recommended_by=recommended_by,
    )


def _topological_order(
    selected: set[str],
    by_name: dict[str, Package],
    requirers: dict[str, set[str]],
    order_hint: list[str] | None = None,
) -> list[str]:
    """kahn topo-sort on requires edges; ties by order_hint then (group, name)."""
    # edge dep -> dependent means dep installs first; in-degree counts requirers within selected
    deps: dict[str, set[str]] = {n: set() for n in selected}
    for prov, reqs in requirers.items():
        if prov not in selected:
            continue
        for r in reqs:
            if r in selected:
                deps[r].add(prov)  # r depends on prov -> prov first

    hint_idx = {n: i for i, n in enumerate(order_hint or [])}
    big = len(hint_idx)

    def rank(n: str) -> tuple[int, int, str]:
        return (hint_idx.get(n, big), _group_rank(by_name[n]), n)

    indeg = {n: len(deps[n]) for n in selected}
    ready = sorted((n for n in selected if indeg[n] == 0), key=rank)
    order: list[str] = []
    # rebuild forward adjacency: prov -> dependents
    fwd: dict[str, set[str]] = {n: set() for n in selected}
    for n in selected:
        for d in deps[n]:
            fwd[d].add(n)
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(fwd[n], key=rank):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort(key=rank)
    if len(order) != len(selected):
        # a requires-cycle: append the remainder in stable order (they co-install). order
        # among them may not honour their edges - log it, it's an authoring error in the yaml.
        leftover = sorted(selected - set(order), key=rank)
        logger.warning(f"requires cycle among {leftover}; install order may be wrong")
        order.extend(leftover)
    return order
