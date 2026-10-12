"""Pure ADEPT/ACSM fulfillment planning (P0 #1120).

Only prepares the runtime contract after borrower/profile checks. No provider
requests, Adobe operations, filesystem mutations, or profile selection occur
here. The service retains both ownership/locking and the execution boundary.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping


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


def profile_is_explicitly_acsm_unready(profile_row: Mapping[str, Any] | None) -> bool:
    """Reject only an explicit negative signal from the selected ADEPT profile.

    Since Kobo-Adept 0.13.3, provisioning.acsm_fetch_ready takes precedence over
    legacy fetch_capable, even if they disagree. Keep older plugin contracts
    working when the new property is absent or unknown. Presence of a reader
    identity alone is not sufficient proof of ACSM capability.

    Caller must already have resolved the borrower-owned profile: this pure
    check never chooses or substitutes a profile, performs I/O, or writes the
    acquisition ledger.
    """
    if not profile_row:
        return False
    provisioning = (
        profile_row.get("provisioning")
        if isinstance(profile_row.get("provisioning"), dict)
        else {}
    )
    readiness = (
        provisioning.get("acsm_fetch_ready")
        if "acsm_fetch_ready" in provisioning
        else profile_row.get("fetch_capable")
    )
    return readiness is False


def plan_produced_format_path(
    produced_path: Path,
    actual_suffix: str,
    *,
    unique_suffix: Callable[[], str],
) -> Path:
    """Choose a non-overwriting filename after ADEPT's actual format is read.

    Read-only: callers remain responsible for the atomic filesystem rename.
    An existing corrected filename must never be silently overwritten. Keep
    UUID allocation lazy and use the service-injected generator.
    """
    if produced_path.suffix.casefold() == actual_suffix:
        return produced_path
    corrected = produced_path.with_suffix(actual_suffix)
    if corrected.exists():
        corrected = corrected.with_name(
            corrected.stem + ".reacquired-" + unique_suffix() + corrected.suffix
        )
    return corrected


def plan_publish_format_path(
    destination: Path,
    actual_suffix: str,
    *,
    repair_mode: bool,
    unique_suffix: Callable[[], str],
) -> Path:
    """Align the caller-owned final name after staging has been renamed.

    Preserve the original sequencing: the caller executes the produced-file
    rename *before* asking for the publish destination. Repair mode retains
    the historical replacement semantics; all other corrected-name collisions
    receive a new filename, without writing to disk in this helper.
    """
    if destination.suffix.casefold() == actual_suffix:
        return destination
    destination = destination.with_suffix(actual_suffix)
    if destination.exists() and not repair_mode:
        destination = destination.with_name(
            destination.stem + ".reacquired-" + unique_suffix() + destination.suffix
        )
    return destination
