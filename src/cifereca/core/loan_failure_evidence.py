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
    # Existing durable receipts retain native integers exactly (even when
    # out of the bounded *diagnostic* projection's range). The public
    # failure_fields() already validates its own smaller ranges separately.
    # Never invoke conversions/comparisons on plugin-defined numeric types.
    for key in ("returncode", "duration_ms", "stdout_bytes", "stderr_bytes"):
        try:
            value = raw.get(key)
        except Exception:
            continue
        if type(value) is int:
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


def adept_failure_outcome(
    *, loan_id: str, status: str, message: str,
    attention_code: str | None, code: str, process: dict[str, Any],
) -> dict[str, Any]:
    """Project the existing ADEPT/provider-classified failure response.

    Values are already classified by the caller; this function does not
    inspect provider exceptions, change retry decisions or publish messages.
    """
    return {
        "status": status, "loan_id": loan_id, "error": message,
        "code": attention_code if status == "attention_required" else code,
        "process": process,
    }


def provider_failure_outcome(
    *, loan_id: str, attention_code: str | None,
) -> dict[str, Any]:
    """The existing generic provider failure response, without side effects."""
    return {
        "status": "retryable_error", "loan_id": loan_id,
        "code": attention_code, "error": "Acquisition du prêt indisponible",
    }


def adept_failure_receipt(
    *, ledger_base: Mapping[str, Any], status: str, executor: str,
    code: str, message: str, metadata: dict[str, Any],
    attention_code: str | None,
) -> dict[str, Any]:
    """Project the existing ADEPT-classified failure ledger row, without I/O.

    Classification, retryability, choice of profile and the single durable
    acquisition_put remain in LibraryService under the existing guards.
    """
    return {
        **ledger_base, "status": status, "strategy": "loan_auto", "executor": executor,
        "error_code": code, "error_message": message, "metadata": metadata,
        "attention_code": None if status == "retryable_error" else attention_code,
    }


def provider_failure_receipt(
    *, ledger_base: Mapping[str, Any], executor: str, validation_failure: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Project the generic provider failure row; never change retry policy."""
    return {
        **ledger_base, "status": "retryable_error", "strategy": "loan_auto",
        "executor": executor,
        "error_code": "artifact_invalid" if validation_failure else "provider_acquisition_failed",
        "error_message": "Acquisition du prêt indisponible",
        "attention_code": None,
        "metadata": metadata,
    }


def public_process_suffix(fields: Mapping[str, Any]) -> str:
    """Format only already-validated public runtime process measurements.

    The caller first applies loan_failure_diagnostics.failure_fields to the
    runtime's private payload. The suffix contains no callback text, paths,
    profile, code guesses or Adobe license inference.
    """
    if type(fields) is not dict:
        return ""
    parts = []
    for key, minimum, maximum in (
        ("returncode", -255, 255),
        ("duration_ms", 0, 86_400_000),
        ("stdout_bytes", 0, 2**63 - 1),
        ("stderr_bytes", 0, 2**63 - 1),
        ("artifact_bytes", 0, 2**63 - 1),
    ):
        value = fields.get(key)
        if type(value) is int and minimum <= value <= maximum:
            parts.append(f"{key}={value}")
    if type(fields.get("artifact_present")) is bool:
        parts.append(f"artifact_present={fields['artifact_present']}")
    return " (" + ", ".join(parts) + ")" if parts else ""
