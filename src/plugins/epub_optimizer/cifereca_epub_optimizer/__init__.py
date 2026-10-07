from __future__ import annotations

from .profiles import DeviceProfile, discover_profiles, profile_from_service

_ENGINE_EXPORTS = {
    "CancelledError",
    "DrmProtectedError",
    "EpubAnalysis",
    "EpubOptimizationError",
    "OptimizationReport",
    "analyze_epub",
    "default_output_path",
    "optimize_epub",
}

def __getattr__(name: str):
    if name in _ENGINE_EXPORTS:
        from . import engine
        return getattr(engine, name)
    raise AttributeError(name)

__all__ = [
    "CancelledError", "DeviceProfile", "DrmProtectedError", "EpubAnalysis",
    "EpubOptimizationError", "OptimizationReport", "analyze_epub",
    "default_output_path", "discover_profiles", "profile_from_service", "optimize_epub",
]
