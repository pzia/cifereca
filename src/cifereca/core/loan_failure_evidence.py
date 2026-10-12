"""Read-only failure evidence for loan acquisition (#1120).

The service owns exception classification, profile choice, diagnostics,
ledger persistence, locking and retries. These projections never perform a
provider/Adobe operation or mutate a file, profile, ledger or input mapping.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


def process_failure_facts(
    raw: Mapping[str, Any], *, exc: BaseException, candidate: Any, path_type: type[Path],
) -> dict[str, Any]:
    """Keep existing typed process facts and bounded local artifact observations."""
    process = {}
    for key in ("returncode", "duration_ms", "stdout_bytes", "stderr_bytes"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            process[key] = value
    exit_code = getattr(exc, "exit_code", None)
    if isinstance(exit_code, int):
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
