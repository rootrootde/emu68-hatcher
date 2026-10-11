"""network tab - stack selection, per-interface IP (dhcp/static), gateway, DNS, wifi creds"""

import re
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from emu68hatcher.config.schema import (
    InterfaceIp,
    IpMode,
    NetworkSettings,
    NetworkStack,
    WifiConfig,
)
from emu68hatcher.data.package_loader import get_package_by_name
from emu68hatcher.gui.design import page_layout
from emu68hatcher.gui.widgets.status_row import StatusRow

# permissive dotted-quad: lets the field be typed; the schema does the real IPv4 check
_IP_RE = QRegularExpression(r"^(\d{1,3})(\.\d{1,3}){0,3}$")


# a fixed label-column width so every group's fields start at the same x and end up equal length
_LABEL_W = 78


def _ip_field(placeholder: str = "") -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setValidator(QRegularExpressionValidator(_IP_RE))
    edit.setMinimumWidth(160)
    return edit


def _flabel(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setMinimumWidth(_LABEL_W)
    return lbl


def _grow_form(group: QGroupBox) -> QFormLayout:
    """a form whose fields fill the available width (macOS defaults to staying at size hint)"""
    form = QFormLayout(group)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


class NetworkTab(QWidget):
    """TCP/IP stack + per-interface address config for ethernet and wifi"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        # scroll wrapper so short windows scroll instead of squashing the field rows
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout = page_layout(content)

        # network stack selection
        net_group = QGroupBox("Network stack")
        net_layout = QVBoxLayout(net_group)
        self.radio_none = QRadioButton("None")
        self.radio_roadshow = QRadioButton("Roadshow")
        self.radio_miamidx = QRadioButton("MiamiDX")
        self.radio_amitcp_ng = QRadioButton("AmiTCP_NG")
        self.radio_roadshow.setChecked(True)
        self.radio_none.setToolTip("No network stack - no online connectivity")
        self.radio_roadshow.setToolTip("Roadshow demo - free TCP/IP stack with PiStorm support")
        self.radio_miamidx.setToolTip("Miami Deluxe 1.0c with GENET and WifiPi profiles")
        net_layout.addWidget(self.radio_none)

        self.radio_roadshow.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        roadshow_row = QHBoxLayout()
        roadshow_row.setContentsMargins(0, 0, 0, 0)
        roadshow_row.setSpacing(6)
        roadshow_row.addWidget(self.radio_roadshow)
        roadshow_row.addStretch()
        net_layout.addLayout(roadshow_row)

        miamidx_row = QHBoxLayout()
        miamidx_row.setContentsMargins(0, 0, 0, 0)
        miamidx_row.setSpacing(6)
        miamidx_row.addWidget(self.radio_miamidx)
        miamidx_row.addStretch()
        net_layout.addLayout(miamidx_row)

        amitcp_ng_row = QHBoxLayout()
        amitcp_ng_row.setContentsMargins(0, 0, 0, 0)
        amitcp_ng_row.setSpacing(6)
        amitcp_ng_row.addWidget(self.radio_amitcp_ng)
        amitcp_ng_row.addStretch()
        net_layout.addLayout(amitcp_ng_row)

        # full-version archive picker: empty -> bundled demo
        self._roadshow_full_box = QWidget()
        full_box_layout = QVBoxLayout(self._roadshow_full_box)
        full_box_layout.setContentsMargins(20, 0, 0, 0)
        full_box_layout.setSpacing(10)
        full_layout = QHBoxLayout()
        full_layout.setSpacing(6)
        full_box_layout.addLayout(full_layout)
        self._roadshow_status = StatusRow()
        full_box_layout.addWidget(self._roadshow_status)
        full_layout.addWidget(QLabel("Full version archive:"))
        self.roadshow_archive_edit = QLineEdit()
        self.roadshow_archive_edit.setPlaceholderText("Roadshow.lha (leave empty for demo)")
        self.roadshow_archive_edit.setReadOnly(True)
        full_layout.addWidget(self.roadshow_archive_edit, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_roadshow_archive)
        full_layout.addWidget(browse_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_roadshow_archive)
        full_layout.addWidget(clear_btn)
        net_layout.addWidget(self._roadshow_full_box)
        self._refresh_roadshow_status()

        self._miamidx_keys_box = QWidget()
        keys_layout = QHBoxLayout(self._miamidx_keys_box)
        keys_layout.setContentsMargins(20, 0, 0, 0)
        keys_layout.setSpacing(6)
        keys_layout.addWidget(QLabel("Registration keys folder:"))
        self.miamidx_key_directory_edit = QLineEdit()
        self.miamidx_key_directory_edit.setPlaceholderText(
            "optional: folder containing all three MiamiDX key files"
        )
        self.miamidx_key_directory_edit.setReadOnly(True)
        keys_layout.addWidget(self.miamidx_key_directory_edit, 1)
        keys_browse_btn = QPushButton("Browse...")
        keys_browse_btn.clicked.connect(self._browse_miamidx_key_directory)
        keys_layout.addWidget(keys_browse_btn)
        keys_clear_btn = QPushButton("Clear")
        keys_clear_btn.clicked.connect(self.miamidx_key_directory_edit.clear)
        keys_layout.addWidget(keys_clear_btn)
        net_layout.addWidget(self._miamidx_keys_box)

        self._miamidx_note = QLabel(
            "MiamiDX uses bundled DHCP profiles. Registration requires all three key files. "
            "Change IP and DNS settings in MiamiDX."
        )
        self._miamidx_note.setWordWrap(True)
        net_layout.addWidget(self._miamidx_note)
        layout.addWidget(net_group)

        # sibling groups share the same gap and can be hidden per stack
        self._ethernet_group = self._build_ethernet_group()
        self._wifi_group = self._build_wifi_group()
        self._routing_group = self._build_routing_group()
        self._iface_groups = [self._ethernet_group, self._wifi_group, self._routing_group]
        for g in self._iface_groups:
            layout.addWidget(g)
        layout.addStretch()

        self.radio_none.toggled.connect(self._update_net_visibility)
        self.radio_roadshow.toggled.connect(self._update_net_visibility)
        self.radio_amitcp_ng.toggled.connect(self._update_net_visibility)
        self.radio_miamidx.toggled.connect(self._update_net_visibility)
        self._update_net_visibility()
        self._update_static_visible()  # hide the IP rows - both interfaces default to DHCP

    def _build_ethernet_group(self) -> QGroupBox:
        group = QGroupBox("Ethernet (genet)")
        form = _grow_form(group)
        self.eth_dhcp = QRadioButton("DHCP")
        self.eth_static = QRadioButton("Static")
        self.eth_dhcp.setChecked(True)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.eth_dhcp)
        mode_row.addWidget(self.eth_static)
        mode_row.addStretch()
        form.addRow(_flabel("Address:"), mode_row)
        self.eth_addr = _ip_field("192.168.1.50")
        self.eth_mask = _ip_field("255.255.255.0")
        form.addRow(_flabel("IP:"), self.eth_addr)
        form.addRow(_flabel("Netmask:"), self.eth_mask)
        self._eth_form = form
        self.eth_static.toggled.connect(self._update_static_visible)
        return group

    def _build_wifi_group(self) -> QGroupBox:
        group = QGroupBox("WiFi (wifipi)")
        form = _grow_form(group)
        self.wifi_ssid = QLineEdit()
        self.wifi_ssid.setPlaceholderText("leave empty to skip wifi")
        self.wifi_ssid.setMaxLength(32)
        form.addRow(_flabel("SSID:"), self.wifi_ssid)
        self.wifi_password = QLineEdit()
        self.wifi_password.setPlaceholderText("empty = open network")
        self.wifi_password.setMaxLength(63)
        self.wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_flabel("Password:"), self.wifi_password)
        self.wifi_dhcp = QRadioButton("DHCP")
        self.wifi_static = QRadioButton("Static")
        self.wifi_dhcp.setChecked(True)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.wifi_dhcp)
        mode_row.addWidget(self.wifi_static)
        mode_row.addStretch()
        form.addRow(_flabel("Address:"), mode_row)
        self._wifi_mode_row = mode_row
        self.wifi_addr = _ip_field("192.168.1.51")
        self.wifi_mask = _ip_field("255.255.255.0")
        form.addRow(_flabel("IP:"), self.wifi_addr)
        form.addRow(_flabel("Netmask:"), self.wifi_mask)
        self._wifi_form = form
        self.wifi_static.toggled.connect(self._update_static_visible)
        return group

    def _build_routing_group(self) -> QGroupBox:
        # one default route + one resolver list are global in roadshow, not per-interface
        # && renders a literal ampersand (a single & is a Qt mnemonic)
        group = QGroupBox("Routing && DNS (shared)")
        form = _grow_form(group)
        self.gw_edit = _ip_field("192.168.1.1")
        form.addRow(_flabel("Gateway:"), self.gw_edit)
        self.dns_edit = QLineEdit()
        self.dns_edit.setPlaceholderText("8.8.8.8 1.1.1.1 (space-separated)")
        form.addRow(_flabel("DNS servers:"), self.dns_edit)
        return group

    def _update_net_visibility(self):
        roadshow = self.radio_roadshow.isChecked()
        amitcp_ng = self.radio_amitcp_ng.isChecked()
        miamidx = self.radio_miamidx.isChecked()
        self._roadshow_full_box.setVisible(roadshow)
        self._miamidx_keys_box.setVisible(miamidx)
        self._miamidx_note.setVisible(miamidx)
        self._ethernet_group.setVisible(roadshow or amitcp_ng)
        self._wifi_group.setVisible(roadshow or amitcp_ng or miamidx)
        self._routing_group.setVisible(roadshow or amitcp_ng)
        self._update_static_visible()

    def _update_static_visible(self):
        eth_on = self.eth_static.isChecked()
        self._eth_form.setRowVisible(self.eth_addr, eth_on)
        self._eth_form.setRowVisible(self.eth_mask, eth_on)
        wifi_address_on = self.radio_roadshow.isChecked() or self.radio_amitcp_ng.isChecked()
        self._wifi_form.setRowVisible(self._wifi_mode_row, wifi_address_on)
        wifi_on = wifi_address_on and self.wifi_static.isChecked()
        self._wifi_form.setRowVisible(self.wifi_addr, wifi_on)
        self._wifi_form.setRowVisible(self.wifi_mask, wifi_on)

    # --- network stack ---
    def get_network_stack(self) -> NetworkStack | None:
        if self.radio_none.isChecked():
            return None
        if self.radio_miamidx.isChecked():
            return NetworkStack.MIAMIDX
        if self.radio_amitcp_ng.isChecked():
            return NetworkStack.AMITCP_NG
        return NetworkStack.ROADSHOW

    def set_network_stack(self, stack: NetworkStack | None):
        if stack is None:
            self.radio_none.setChecked(True)
        elif stack == NetworkStack.AMITCP_NG:
            self.radio_amitcp_ng.setChecked(True)
        elif stack == NetworkStack.MIAMIDX:
            self.radio_miamidx.setChecked(True)
        else:
            self.radio_roadshow.setChecked(True)
        self._update_net_visibility()

    def extra_package_entries(self) -> list[dict]:
        """the network-stack package the tree doesn't carry"""
        stack = self.get_network_stack()
        if stack is not None:
            return [{"name": stack.value.lower(), "enabled": True}]
        return []

    # --- per-interface settings ---
    def get_network_settings(self) -> NetworkSettings:
        # only read the IP fields in static mode, so a stale/half-typed value left in a
        # dhcp-mode field never reaches (and fails) schema validation
        def _iface(static: QRadioButton, addr: QLineEdit, mask: QLineEdit) -> InterfaceIp:
            if not static.isChecked():
                return InterfaceIp(mode=IpMode.DHCP)
            return InterfaceIp(
                mode=IpMode.STATIC,
                address=addr.text().strip() or None,
                netmask=mask.text().strip() or None,
            )

        eth = _iface(self.eth_static, self.eth_addr, self.eth_mask)
        wifi = _iface(self.wifi_static, self.wifi_addr, self.wifi_mask)
        dns = [s for s in re.split(r"[\s,]+", self.dns_edit.text().strip()) if s]
        return NetworkSettings(
            ethernet=eth, wifi=wifi, gateway=self.gw_edit.text().strip() or None, dns_servers=dns
        )

    def set_network_settings(self, net: NetworkSettings):
        def _apply(static_rb, dhcp_rb, addr, mask, iface):
            static_rb.setChecked(iface.mode == IpMode.STATIC)
            dhcp_rb.setChecked(iface.mode != IpMode.STATIC)
            addr.setText(iface.address or "")
            mask.setText(iface.netmask or "")

        _apply(self.eth_static, self.eth_dhcp, self.eth_addr, self.eth_mask, net.ethernet)
        _apply(self.wifi_static, self.wifi_dhcp, self.wifi_addr, self.wifi_mask, net.wifi)
        self.gw_edit.setText(net.gateway or "")
        self.dns_edit.setText(" ".join(net.dns_servers))
        self._update_static_visible()

    # --- roadshow archive ---
    def _browse_roadshow_archive(self):
        from PySide6.QtWidgets import QFileDialog

        start = self.roadshow_archive_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Roadshow archive",
            start,
            "Roadshow archive (*.lha *.LHA);;All files (*.*)",
        )
        if path:
            self.roadshow_archive_edit.setText(path)
            self._refresh_roadshow_status()

    def _clear_roadshow_archive(self):
        self.roadshow_archive_edit.clear()
        self._refresh_roadshow_status()

    def _refresh_roadshow_status(self):
        path = self.roadshow_archive_edit.text().strip()
        if path:
            archive = Path(path)
            if archive.is_file():
                self._roadshow_status.set_status("ok", "Roadshow full version", archive.name)
            else:
                self._roadshow_status.set_status("warning", "Archive not found", path)
            return
        roadshow_pkg = get_package_by_name("roadshow")
        detail = "Add the full version archive to remove the demo limits."
        if roadshow_pkg and roadshow_pkg.purchase_url:
            detail += f' <a href="{roadshow_pkg.purchase_url}">Buy Roadshow</a>'
        self._roadshow_status.set_status("info", "Roadshow demo", detail)

    def get_roadshow_archive(self) -> Path | None:
        text = self.roadshow_archive_edit.text().strip()
        return Path(text) if text else None

    def set_roadshow_archive(self, archive: Path | str | None):
        self.roadshow_archive_edit.setText(str(archive) if archive else "")
        self._refresh_roadshow_status()

    # --- MiamiDX keys ---
    def _browse_miamidx_key_directory(self):
        from PySide6.QtWidgets import QFileDialog

        start = self.miamidx_key_directory_edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select MiamiDX keys folder", start)
        if path:
            self.miamidx_key_directory_edit.setText(path)

    def get_miamidx_key_directory(self) -> Path | None:
        text = self.miamidx_key_directory_edit.text().strip()
        return Path(text) if text else None

    def set_miamidx_key_directory(self, directory: Path | str | None):
        self.miamidx_key_directory_edit.setText(str(directory) if directory else "")

    # --- wifi creds ---
    def get_wifi_config(self) -> WifiConfig | None:
        """wifi creds, or None when no SSID; empty password means an open network"""
        ssid = self.wifi_ssid.text().strip()
        if not ssid:
            return None
        password = self.wifi_password.text().strip()
        if password and len(password) < 8:
            return None  # WPA needs >=8; treat a too-short password as incomplete
        return WifiConfig(ssid=ssid, password=password)

    def set_wifi_config(self, wifi: WifiConfig | None):
        if wifi:
            self.wifi_ssid.setText(wifi.ssid)
            self.wifi_password.setText(wifi.password)
        else:
            self.wifi_ssid.clear()
            self.wifi_password.clear()
