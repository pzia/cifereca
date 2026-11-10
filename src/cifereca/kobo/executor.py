"""P0 #4858/#3196: archived Kobo D3 compatibility ABI, NO executable GET.

Kobo supplier rights, descriptors, KDRM, ADEPT and content downloads now
belong to the selected provider plugin and Core's canonical SourceKey→B
pipeline. No 0.7 direct code path may consume a SearchResult as possession
or start a content GET, even when imported directly outside LibraryService.
This module remains importable temporarily for historical callers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..acquisition import Acquisition
from ..models import SearchResult
from ..providers import ProviderConfigurationError


def _provider_delegation_admitted(session: Any) -> bool:
    """Historical negative guard; never interprets a capability as authority."""
    if getattr(session, "supports_acquisition_delegation", None) is not True:
        return False
    probe = getattr(session, "_delegation_admitted", None)
    if probe is None:
        return True  # Historical attestation API; not a GET permit.
    if not callable(probe):
        return False
    try:
        return probe() is True
    except Exception:
        return False


def execute_kobo_acquisition(
    service: Any, item: SearchResult, target_dir: Path,
) -> Acquisition:
    """Retired D3 entry: fail before reading session, files or provider data."""
    raise ProviderConfigurationError(
        "kobo_legacy_direct_get_retired_use_canonical_provider"
    )
