"""Markers for supported AGS portable scripts."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .errors import BuildError


@dataclass(frozen=True, slots=True)
class AGSProfile:
    name: str
    version: str
    marker_hashes: Mapping[str, str]


AGS_PROFILES = MappingProxyType(
    {
        "v30": AGSProfile(
            "v30",
            "3.0",
            MappingProxyType(
                {
                    "AGS2/Scripts/Check_Drives": "fb32d97ebcfb4b70f9229dbf55d8fcb2144f077cd7655d536f49e7033bacf032",
                    "AGS2/Scripts/Speed_Reset": "0de95c958dbce0ff737b4fc7c9ab676dce7443c1874844d63da3818f6467e93a",
                    "AGS2/Scripts/Start_AGS": "b88265909e2c57fbfa93a89c2bdd46aec764be956d6e7f1055419b1e9a5339a9",
                    "AGS2/Scripts/Start_AGS.info": "4a0345116b53587039fe2a42111b7f04e7d197ce45964fc9a5a8e123c803b67f",
                }
            ),
        ),
        "v31-beta-160726": AGSProfile(
            "v31-beta-160726",
            "3.1 beta 160726",
            MappingProxyType(
                {
                    "AGS2/Scripts/Check_Drives": "d536a39b2f9eacd36c0236108d9e6237ecb5923e5ec5a8dc585d61ea509dc59b",
                    "AGS2/Scripts/Speed_Reset": "21006f5f24e0f49668e872ee358b1a5f7ad679e2f878d96e1811ea2ea13918e9",
                    "AGS2/Scripts/Start_AGS": "459181492bc4a13e6161ce1996ade79e417386c5fd65a19269d44a8ae48b0b7c",
                    "AGS2/Scripts/Start_AGS.info": "25870f8e66d145955fe0f51ef954a39bfe227c03a7510c95320d90c6091d324c",
                }
            ),
        ),
    }
)


def get_profile(profile: str | AGSProfile) -> AGSProfile:
    if isinstance(profile, AGSProfile):
        return profile
    try:
        return AGS_PROFILES[profile]
    except KeyError as exc:
        raise BuildError(f"Unsupported AGS script profile: {profile}") from exc


def match_profile(marker_hashes: Mapping[str, str]) -> AGSProfile:
    actual = {name.casefold(): digest.casefold() for name, digest in marker_hashes.items()}
    for profile in AGS_PROFILES.values():
        if all(
            actual.get(path.casefold()) == expected
            for path, expected in profile.marker_hashes.items()
        ):
            return profile
    for path in next(iter(AGS_PROFILES.values())).marker_hashes:
        digest = actual.get(path.casefold())
        if digest is None:
            raise BuildError(f"AGS script profile is missing marker: {path}")
        if all(digest != profile.marker_hashes[path] for profile in AGS_PROFILES.values()):
            raise BuildError(f"AGS script profile is unsupported: {path} differs")
    raise BuildError("AGS script markers combine different supported releases")
