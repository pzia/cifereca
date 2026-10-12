"""Pure ADEPT/ACSM fulfillment planning (P0 #1120).

Only prepares the runtime contract after borrower/profile checks. No provider
requests, Adobe operations, filesystem mutations, or profile selection occur
here. The service retains both ownership/locking and the execution boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


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


def plan_adept_destination(
    destination: Path,
    *,
    force_refresh: bool,
    legacy_acsm_artifact: bool,
    previous_path: Path | None,
    unique_suffix: Callable[[], str],
) -> tuple[Path, Path, bool]:
    """Choose ADEPT publish/staging paths without filesystem mutations.

    Preserve the existing loan-specific policy exactly: a legacy ADEPT
    artifact may be repaired through a temporary '.repairing' path only
    when automatic (not forced) and the prior file still exists. Otherwise,
    a colliding destination receives a fresh '.reacquired-<id>' filename
    instead of overwriting a potentially unrelated or historical book.

    The service retains authority for directory creation, preflight unlink,
    ADEPT execution, inspection, publish/rename and cleanup while under the
    same inter-process and in-process locks.
    """
    repair_mode = bool(
        not force_refresh and legacy_acsm_artifact
        and previous_path is not None and previous_path.is_file()
    )
    if not repair_mode and destination.exists():
        destination = destination.with_name(
            destination.stem + ".reacquired-" + unique_suffix() + destination.suffix
        )
    adept_destination = (
        destination.with_name(destination.stem + ".repairing" + destination.suffix)
        if repair_mode else destination
    )
    return destination, adept_destination, repair_mode
