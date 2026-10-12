"""Pure ADEPT/ACSM fulfillment planning (P0 #1120).

Only prepares the runtime contract after borrower/profile checks. No provider
requests, Adobe operations, filesystem mutations, or profile selection occur
here. The service retains both ownership/locking and the execution boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def select_acsm_format(detected_format: str | None) -> tuple[str, str, str]:
    """Match the historical ACSM format fallback and filename suffix.

    Contract v8 needs an exact caller-owned destination. The EPUB fallback is
    used only when no format metadata is available (None); an explicitly
    returned format must not be silently replaced or normalized here.
    """
    if detected_format is None:
        acsm_format = "epub"
        format_source = "fallback"
    else:
        acsm_format = detected_format
        format_source = "acsm"
    suffix = ".pdf" if acsm_format == "pdf" else ".epub"
    return acsm_format, format_source, suffix


def build_fulfillment_request(
    *,
    acsm_path: Path,
    destination: Path,
    acsm_format: str,
    profile_name: str,
    provider: str,
    title: str,
    loan_id: str,
) -> dict[str, Any]:
    """Build the existing v2 Kobo-Adept request without invoking the runtime.

    The caller verifies the pinned Paris borrower grant, resolves the ADEPT
    profile, checks readiness, and chooses a safe destination *before* calling.
    Kobo-Adept owns post-download processing: do not override run_hook or
    enable force, and never pass a provider's private HTTP headers.
    """
    return {
        "schema_version": 2,
        "source": {
            "type": "acsm",
            "provider": provider,
            "path": str(acsm_path),
            "headers": {},
        },
        "destination": str(destination),
        "format": acsm_format,
        "profile": profile_name,
        "metadata": {"title": title, "loan_id": loan_id},
        "options": {"force": False},
    }
