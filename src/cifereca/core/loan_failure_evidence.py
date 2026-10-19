"""Read-only failure evidence for loan acquisition (#1120).

The service owns exception classification, profile choice, diagnostics,
ledger persistence, locking and retries. These projections never perform a
provider/Adobe operation or mutate a file, profile, ledger or input mapping.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def safe_exit_code(exc: BaseException) -> int | None:
    """Inspect a provider exception without executing numeric coercions."""
    try:
        value = getattr(exc, "exit_code", None)
    except Exception:
        return None
    return value if type(value) is int and -255 <= value <= 255 else None


def safe_retryable_flag(exc: BaseException) -> bool:
    """Only an explicit native boolean can request the historical retry."""
    try:
        return getattr(exc, "retryable", False) is True
    except Exception:
        return False


def process_failure_facts(
    raw: Mapping[str, Any], *, exc: BaseException, candidate: Any, path_type: type[Path],
) -> dict[str, Any]:
    """Keep bounded native process facts; never stringify provider values."""
    process = {}
    limits = (
        ("returncode", -255, 255),
        ("duration_ms", 0, 86_400_000),
        ("stdout_bytes", 0, 2**63 - 1),
        ("stderr_bytes", 0, 2**63 - 1),
    )
    for key, minimum, maximum in limits:
        try:
            value = raw.get(key)
        except Exception:
            continue
        if type(value) is int and minimum <= value <= maximum:
            process[key] = value
    exit_code = safe_exit_code(exc)
    if exit_code is not None:
        process["returncode"] = exit_code
    if isinstance(candidate, path_type):
        try:
            process["artifact_present"] = candidate.is_file()
            if process["artifact_present"]:
                process["artifact_bytes"] = candidate.stat().st_size
        except OSError:
            process["artifact_present"] = False
    return process


def adept_failure_metadata(
    previous_meta: Mapping[str, Any], *, loan_id: str, process: dict[str, Any],
    retained_acsm: Path | None, executor: str, attempt_profile: Any,
) -> dict[str, Any]:
    metadata = {**previous_meta, "provider_loan_id": loan_id, "process": process}
    if retained_acsm is not None:
        metadata["acsm_staging_path"] = str(retained_acsm)
    if executor == "kobo-adept":
        if isinstance(attempt_profile, str):
            metadata["adept_attempt_profile"] = attempt_profile
        metadata["failure_stage"] = "adept_fulfillment"
    return metadata


def provider_failure_metadata(
    previous_meta: Mapping[str, Any], *, loan_id: str, retained_acsm: Path | None,
    attention_code: str | None, validation_failure: bool,
) -> dict[str, Any]:
    metadata = {**previous_meta, "provider_loan_id": loan_id}
    if retained_acsm is not None:
        metadata["acsm_staging_path"] = str(retained_acsm)
    if attention_code:
        metadata["provider_condition"] = attention_code
    if validation_failure:
        metadata.update({"repairable": True, "failure_stage": "post_adept_validation"})
    return metadata
