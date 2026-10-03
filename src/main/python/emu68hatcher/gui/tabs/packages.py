"""Software requests and their resolved dependencies."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.schema import NetworkStack, PackageConfig
from emu68hatcher.data.package_loader import (
    get_bundle_members,
    get_bundles_for_version,
    get_packages_for_version,
    load_all_packages,
)
from emu68hatcher.data.package_selection import resolve_choices, software_defaults

_NETWORK_STACK_PACKAGES = {stack.value.lower() for stack in NetworkStack}


class PackagesTab(QWidget):
    minimal_requested = Signal()
    selection_changed = Signal()

    def __init__(self, parent=None, kickstart_version="3.2.3", emu68_version=None):
        super().__init__(parent)
        self.kickstart_version = kickstart_version
        self.emu68_version = emu68_version
        self.network_stack = None
        self._requests = software_defaults()
        self._updating = False
        self.checkboxes: dict[str, QTreeWidgetItem] = {}
        self._key_to_packages: dict[str, list[str]] = {}
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        for label, callback in (
            ("Select All", self.select_all),
            ("Select None", self.select_none),
            ("Defaults", self.select_defaults),
            ("Minimal", self.minimal_requested.emit),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        note = QLabel(
            "Minimal keeps the OS, RTG and FirstBoot tools; optional software and networking "
            "are disabled. Partition extra content is still copied."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Package", "Description / dependency"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)
        self.catalog_notice = QLabel()
        self.catalog_notice.setWordWrap(True)
        layout.addWidget(self.catalog_notice)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_packages()

    def refresh_packages(self):
        self._updating = True
        try:
            self._refresh_packages()
        finally:
            self._updating = False

    def _refresh_packages(self):
        expanded = {
            self.tree.topLevelItem(i).text(0): self.tree.topLevelItem(i).isExpanded()
            for i in range(self.tree.topLevelItemCount())
        }
        self.tree.clear()
        self.checkboxes.clear()
        self._key_to_packages.clear()
        packages = get_packages_for_version(self.kickstart_version, self.emu68_version)
        by_name = {p.name: p for p in packages}
        self.resolution = resolve_choices(
            self._requests,
            self.kickstart_version,
            self.emu68_version,
            self.network_stack,
        )
        selected = self.resolution.selected
        groups = {}
        bold = QFont()
        bold.setBold(True)

        def group(label):
            if label not in groups:
                item = QTreeWidgetItem(self.tree, [label, ""])
                item.setFont(0, bold)
                item.setExpanded(
                    expanded.get(label, label not in {"Libraries", "Required packages"})
                )
                groups[label] = item
            return groups[label]

        def add_row(key, label, category, description, names):
            enabled = [name in selected for name in names]
            reasons = set()
            for name in names:
                reasons.update(self.resolution.required_by.get(name, []))
            reasons.difference_update(names)
            auto = any(name in selected and not self._requests.get(name, False) for name in names)
            forced = {
                self.resolution.selection_reasons[n]
                for n in names
                if n in self.resolution.selection_reasons
            }
            recommendations = {
                n for name in names for n in self.resolution.recommended_by.get(name, [])
            }
            if reasons:
                consumers = ", ".join(by_name[n].friendly_name for n in sorted(reasons))
                description += f" (Required by {consumers})"
            elif forced:
                description += f" (Required by {', '.join(sorted(forced))})"
            elif recommendations and auto:
                description += (
                    " (Recommended by "
                    + ", ".join(by_name[n].friendly_name for n in sorted(recommendations))
                    + ")"
                )
            elif auto:
                description += " (Selected by network or recommendation)"
            item = QTreeWidgetItem(group(category), [label, description])
            item.setToolTip(1, description)
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # mui alternatives stay clickable while an application needs mui
            if (reasons or forced) and key not in {"mui38", "mui5"}:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            state = Qt.CheckState.Checked if all(enabled) else Qt.CheckState.Unchecked
            if any(enabled) and not all(enabled):
                state = Qt.CheckState.PartiallyChecked
            item.setCheckState(0, state)
            self.checkboxes[key] = item
            self._key_to_packages[key] = names

        for bundle in get_bundles_for_version(self.kickstart_version, self.emu68_version):
            members = get_bundle_members(bundle.id, self.kickstart_version, self.emu68_version)
            add_row(
                "bundle:" + bundle.id,
                bundle.display_name,
                bundle.group,
                bundle.description or "",
                [p.name for p in members],
            )
        for pkg in packages:
            if pkg.mandatory or pkg.bundle or pkg.name in _NETWORK_STACK_PACKAGES:
                continue
            if pkg.name in {"mui38", "mui5"}:
                category = "MUI Toolkit"
            elif pkg.group in {"System", "Locale"}:
                continue
            else:
                category = pkg.group
            add_row(pkg.name, pkg.friendly_name, category, pkg.description, [pkg.name])

        shown = {name for names in self._key_to_packages.values() for name in names}
        for name in sorted(selected - shown):
            pkg = by_name[name]
            if not (pkg.install or pkg.relocate or pkg.scripts or pkg.mandatory):
                continue
            reasons = self.resolution.required_by.get(name, [])
            description = "Required by " + ", ".join(by_name[n].friendly_name for n in reasons)
            if pkg.mandatory:
                description = "Required for the Hatcher base system"
            elif not reasons:
                description = "Selected by network or locale"
            QTreeWidgetItem(group("Required packages"), [pkg.friendly_name, description])
        problems = [
            f"{token}: needed by {', '.join(names)}"
            for token, names in self.resolution.unsatisfiable.items()
        ]
        self.status.setText(
            "Missing requirements: " + "; ".join(problems)
            if problems
            else "Dependencies are included automatically; saved choices stay separate."
        )

    def _on_item_changed(self, item, column):
        if self._updating or column != 0:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key not in self._key_to_packages:
            return
        enabled = item.checkState(0) == Qt.CheckState.Checked
        for name in self._key_to_packages[key]:
            self._requests[name] = enabled
        if enabled and key in {"mui38", "mui5"}:
            self._requests["mui5" if key == "mui38" else "mui38"] = False
        self.refresh_packages()
        self.selection_changed.emit()

    def set_context(self, network_stack):
        self.network_stack = network_stack
        self.refresh_packages()

    def set_kickstart_version(self, version):
        if version != self.kickstart_version:
            self.kickstart_version = version
            self.refresh_packages()

    def set_emu68_version(self, version):
        if version != self.emu68_version:
            self.emu68_version = version
            self.refresh_packages()

    def select_all(self):
        for names in self._key_to_packages.values():
            for name in names:
                self._requests[name] = True
        self._requests["mui38"] = False
        self._requests["mui5"] = True
        self.refresh_packages()
        self.selection_changed.emit()

    def select_none(self):
        self._requests = dict.fromkeys(software_defaults(), False)
        self.refresh_packages()
        self.selection_changed.emit()

    def select_defaults(self):
        self._requests = software_defaults()
        self.refresh_packages()
        self.selection_changed.emit()

    def get_config(self) -> list[dict]:
        return [{"name": name, "enabled": enabled} for name, enabled in self._requests.items()]

    def refresh_catalog(self):
        defaults = software_defaults()
        known = {p.name for p in load_all_packages()}
        removed = sorted(
            name for name, enabled in self._requests.items() if enabled and name not in known
        )
        self._requests = {
            name: self._requests.get(name, enabled) for name, enabled in defaults.items()
        }
        self.catalog_notice.setText(
            "Previously selected packages are no longer available: " + ", ".join(removed)
            if removed
            else ""
        )
        self.refresh_packages()
        self.selection_changed.emit()

    def set_config(self, packages: list[PackageConfig]):
        self._requests = software_defaults()
        known = {p.name for p in load_all_packages()}
        unknown = sorted(p.name for p in packages if p.enabled and p.name not in known)
        self.catalog_notice.setText(
            "Saved packages are no longer available: " + ", ".join(unknown) if unknown else ""
        )
        self._requests.update({p.name: p.enabled for p in packages if p.name in self._requests})
        if self._requests.get("mui5"):
            self._requests["mui38"] = False
        self.refresh_packages()
