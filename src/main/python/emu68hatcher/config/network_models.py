"""Network configuration models."""

import ipaddress
from enum import Enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


class NetworkStack(str, Enum):
    ROADSHOW = "Roadshow"
    AMITCP_NG = "AmiTCP_NG"
    MIAMIDX = "MiamiDX"


class WifiConfig(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)

    model_config = ConfigDict(extra="forbid")

    @field_validator("ssid", "password")
    @classmethod
    def _no_control_chars(cls, value: str, info: ValidationInfo) -> str:
        if any(ord(c) < 32 or c == "\x7f" for c in value):
            label = "SSID" if info.field_name == "ssid" else "WiFi password"
            raise ValueError(f"{label} must not contain control characters")
        return value


class IpMode(str, Enum):
    DHCP = "dhcp"
    STATIC = "static"


def _ipv4_or_none(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    ipaddress.IPv4Address(value)
    return value


class InterfaceIp(BaseModel):
    mode: IpMode = IpMode.DHCP
    address: str | None = None
    netmask: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("address", "netmask", mode="before")
    @classmethod
    def _check_ipv4(cls, value):
        return _ipv4_or_none(value)

    @model_validator(mode="after")
    def _require_static_fields(self):
        if self.mode == IpMode.STATIC and (not self.address or not self.netmask):
            raise ValueError("a static interface needs both an address and a netmask")
        return self


class NetworkSettings(BaseModel):
    ethernet: InterfaceIp = Field(default_factory=InterfaceIp)
    wifi: InterfaceIp = Field(default_factory=InterfaceIp)
    gateway: str | None = None
    dns_servers: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("gateway", mode="before")
    @classmethod
    def _check_gateway(cls, value):
        return _ipv4_or_none(value)

    @field_validator("dns_servers", mode="before")
    @classmethod
    def _check_dns(cls, value):
        if not value:
            return []
        return [server for server in (_ipv4_or_none(item) for item in value) if server]
