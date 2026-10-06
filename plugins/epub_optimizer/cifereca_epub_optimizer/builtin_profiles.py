from __future__ import annotations

from .profiles import DeviceProfile


class DeviceProfilePlugin:
    def __init__(self, profile: DeviceProfile) -> None:
        self.profile = profile
        self.context = None

    def activate(self, context):
        self.context = context
        return self

    def describe(self):
        # Public device.profile contract projection. Keep the dataclass private to
        # this plugin; consumers receive plain structural data.
        profile = self.profile
        return {
            "id": profile.id, "label": profile.label, "width": profile.width, "height": profile.height,
            "grayscale": profile.grayscale, "gray_levels": profile.gray_levels,
            "autocontrast_cutoff": profile.autocontrast_cutoff, "contrast_factor": profile.contrast_factor,
            "jpeg_quality": profile.jpeg_quality, "suffix": profile.suffix, "description": profile.description,
        }

    def deactivate(self) -> None:
        self.context = None


# Hardware policy lives in independent profile plugins, not in the transformer.
XTEINK_X3_PLUGIN = DeviceProfilePlugin(DeviceProfile(
    id="xteink-x3",
    label="Xteink X3 / CrossPoint (528×792, 4 gris)",
    width=528,
    height=792,
    grayscale=True,
    gray_levels=(0, 85, 170, 255),
    autocontrast_cutoff=1.0,
    contrast_factor=1.50,
    jpeg_quality=70,
    suffix="X3",
    description="Profil très compact pour X3/CrossPoint : 4 niveaux de gris avec tramage.",
))

XTEINK_X4_PLUGIN = DeviceProfilePlugin(DeviceProfile(
    id="xteink-x4",
    label="Xteink X4 / CrossPoint (480×800, 4 gris)",
    width=480,
    height=800,
    grayscale=True,
    gray_levels=(0, 85, 170, 255),
    autocontrast_cutoff=1.0,
    contrast_factor=1.50,
    jpeg_quality=70,
    suffix="X4",
    description="Profil très compact pour X4/CrossPoint : 4 niveaux de gris avec tramage.",
))

KOBO_CLARA_BW_PLUGIN = DeviceProfilePlugin(DeviceProfile(
    id="kobo-clara-bw",
    label="Kobo Clara BW (1072×1448, 300 ppp)",
    width=1072,
    height=1448,
    grayscale=True,
    gray_levels=None,
    autocontrast_cutoff=0.5,
    contrast_factor=1.05,
    jpeg_quality=82,
    suffix="Clara BW",
    description="Préserve les dégradés 8 bits tout en supprimant la couleur inutile et les pixels au-delà de l'écran.",
))

GENERIC_EINK_PLUGIN = DeviceProfilePlugin(DeviceProfile(
    id="generic-eink",
    label="E-ink générique (1200×1600, gris)",
    width=1200,
    height=1600,
    grayscale=True,
    gray_levels=None,
    autocontrast_cutoff=None,
    contrast_factor=1.0,
    jpeg_quality=80,
    suffix="eink",
    description="Profil prudent pour liseuse noir et blanc de résolution inconnue.",
))

BUILTIN_PROFILE_PLUGINS = (
    XTEINK_X3_PLUGIN,
    XTEINK_X4_PLUGIN,
    KOBO_CLARA_BW_PLUGIN,
    GENERIC_EINK_PLUGIN,
)


def builtin_profile(profile_id: str) -> DeviceProfile:
    for plugin in BUILTIN_PROFILE_PLUGINS:
        if plugin.profile.id == profile_id:
            return plugin.profile
    raise ValueError(f"unknown built-in profile: {profile_id}")


__all__ = [
    "DeviceProfilePlugin", "XTEINK_X3_PLUGIN", "XTEINK_X4_PLUGIN",
    "KOBO_CLARA_BW_PLUGIN", "GENERIC_EINK_PLUGIN", "BUILTIN_PROFILE_PLUGINS",
    "builtin_profile",
]
