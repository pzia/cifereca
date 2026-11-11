"""Deprecated compatibility import for Paris ADEPT runtime presentation.

The observation is provider-neutral and owned by Core's ADEPT runtime.
"""
from __future__ import annotations

from .adept_runtime_observation import describe_paris_adept_runtime

__all__ = ["describe_paris_adept_runtime"]
