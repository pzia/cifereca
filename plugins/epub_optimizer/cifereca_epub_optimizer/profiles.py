from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Optimizer-facing projection of a public ``device.profile`` service.

    Third-party profile plugins do not have to import this class. A service can
    expose either ``service.profile``, a mapping, or matching public attributes.
    """

    id: str
    label: str
    width: int
    height: int
    max_zoom_factor: float | None
    allow_landscape: bool
    grayscale: bool
    gray_levels: tuple[int, ...] | None
    autocontrast_cutoff: float | None
    contrast_factor: float
    jpeg_quality: int
    suffix: str
    description: str
    plugin_id: str = ""
    plugin_version: str = ""


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def profile_from_service(service: Any, *, plugin_id: str = "", plugin_version: str = "") -> DeviceProfile:
    """Normalize a discovered device profile without depending on its package."""
    describe = getattr(service, "describe", None)
    if callable(describe):
        raw = describe()
    else:
        # Plugin API 1.1 compatibility for profiles published before
        # device.profile.describe became the canonical capability.
        raw = getattr(service, "profile", service)
    profile_id = str(_value(raw, "id", "") or plugin_id).strip()
    label = str(_value(raw, "label", "") or profile_id).strip()
    width = int(_value(raw, "width", 0) or 0)
    height = int(_value(raw, "height", 0) or 0)
    if not profile_id or width <= 0 or height <= 0:
        raise ValueError("device.profile service must expose id, width and height")

    raw_zoom = _value(raw, "max_zoom_factor", None)
    max_zoom_factor = None if raw_zoom in {None, ""} else float(raw_zoom)
    if max_zoom_factor is not None and max_zoom_factor < 1.0:
        raise ValueError("invalid device profile max_zoom_factor")

    gray_levels = _value(raw, "gray_levels", None)
    if gray_levels is not None:
        gray_levels = tuple(int(value) for value in gray_levels)
        if len(gray_levels) < 2 or any(value < 0 or value > 255 for value in gray_levels):
            raise ValueError("invalid device profile gray_levels")

    return DeviceProfile(
        id=profile_id,
        label=label,
        width=width,
        height=height,
        max_zoom_factor=max_zoom_factor,
        allow_landscape=bool(_value(raw, "allow_landscape", False)),
        grayscale=bool(_value(raw, "grayscale", True)),
        gray_levels=gray_levels,
        autocontrast_cutoff=(
            None if _value(raw, "autocontrast_cutoff", None) is None
            else float(_value(raw, "autocontrast_cutoff"))
        ),
        contrast_factor=float(_value(raw, "contrast_factor", 1.0) or 1.0),
        jpeg_quality=max(1, min(95, int(_value(raw, "jpeg_quality", 80) or 80))),
        suffix=str(_value(raw, "suffix", "") or label or profile_id).strip(),
        description=str(_value(raw, "description", "") or "").strip(),
        plugin_id=str(plugin_id or _value(raw, "plugin_id", "") or "").strip(),
        plugin_version=str(plugin_version or _value(raw, "plugin_version", "") or "").strip(),
    )


def discover_profiles(context: Any) -> dict[str, DeviceProfile]:
    """Return enabled/resolved profiles keyed by stable plugin id."""
    found: dict[str, DeviceProfile] = {}
    for resolved in context.services(
        "device.profile",
        contract="cifereca.device.profile",
        version=">=1,<2",
        capabilities=("device.profile.describe",),
    ):
        try:
            profile = profile_from_service(
                resolved.service,
                plugin_id=resolved.plugin_id,
                plugin_version=resolved.plugin_version,
            )
        except Exception as exc:
            context.logger.warning("Ignoring invalid device profile %s: %s", resolved.plugin_id, type(exc).__name__)
            continue
        found[resolved.plugin_id] = profile
    return found


def default_output_path(input_path: str | Path, profile: DeviceProfile) -> Path:
    source = Path(input_path)
    return source.with_name(f"{source.stem} [{profile.suffix}].epub")


__all__ = ["DeviceProfile", "profile_from_service", "discover_profiles", "default_output_path"]
