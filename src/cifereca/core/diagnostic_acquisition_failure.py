"""#3632: preserve existing acquisition observations, excluding credentials.

This is a formatter adapter, not another emitter or an acquisition observer.
Only exact existing Core log templates are admitted. Provider arguments are
observations, not registry attestations; phases/HTTP calls are never guessed.
"""
from __future__ import annotations

import logging
import re
import traceback

from ..diagnostic_task_context import diagnostic_task_reference

_TEMPLATES = {
    "TRACE explicit loan recovery attention required provider=%s loan_id=%s": ("attention_required", "terminal"),
    "TRACE explicit loan recovery admission refused provider=%s loan_id=%s code=%s": ("refused", "admission"),
    "TRACE explicit loan recovery provider failure provider=%s loan_id=%s": ("failed", "terminal"),
    "TRACE explicit loan recovery unexpected failure provider=%s loan_id=%s": ("failed", "terminal"),
}
_INNER = "TRACE download provider error loan_ref=%s reason=provider_or_artifact_failure dt=%.3fs"
_EXTRA = {
    "TRACE download loan_auto blocked reason=unbound_connection_identity request_ref=%s":
        ("refused", "admission", "automatic_admission_refusal", ("request_ref",), "unbound_connection_identity"),
    "TRACE download loan_auto blocked reason=cross_identity_loan loan_ref=%s":
        ("refused", "admission", "automatic_admission_refusal", ("loan_ref",), "cross_identity_loan"),
    "TRACE download loan_auto blocked reason=borrower_binding_guard loan_ref=%s":
        ("refused", "admission", "automatic_admission_refusal", ("loan_ref",), "borrower_binding_guard"),
    "TRACE bnf auto receipt file check failed provider_id=%s identity_id=%s credential_id=%s":
        ("unknown", None, "local_evidence_failure", ("observed_provider_id", "identity_id", "credential_id"), None),
}
for _stage in ("declared_lookup", "indexed_candidates", "physical_candidates", "declared_revalidation", "delivery_or_import"):
    _kind = "acquisition" if _stage == "delivery_or_import" else "local evidence"
    _EXTRA[f"TRACE bnf auto {_kind} failed stage={_stage} provider_id=%s credential_id=%s account_id=%s loan_id=%s"] = (
        "failed" if _stage == "delivery_or_import" else "unknown", _stage,
        "automatic_acquisition_failure" if _stage == "delivery_or_import" else "local_evidence_failure",
        ("observed_provider_id", "credential_id", "account_id", "loan_id"), None,
    )
_SECRET_KEYS = (
    r"password|passwd|pwd|passphrase|access[_-]?token|refresh[_-]?token|id[_-]?token|token|"
    r"api[_-]?key|x-api-key|client[_-]?secret|private[_-]?key|activation[_-]?token|"
    r"license[_-]?key|signature|sig|x-amz-signature|x-goog-signature|"
    r"x-amz-security-token|authorization|proxy-authorization"
)
_ASSIGNMENT = re.compile(
    rf'''(?ix)(?<![\w.?&-])(?P<prefix>["']?(?:{_SECRET_KEYS})["']?\s*[:=]\s*)'''
    r'''(?P<value>\[SECRET\]|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,<>"'\}\]]+)'''
)
_QUERY = re.compile(
    rf'''(?ix)([?&](?:{_SECRET_KEYS})=)([^&#\s"'<>]*)'''
)
_COOKIE = re.compile(
    r'''(?ix)(?<![\w.-])((?:authorization|proxy-authorization|set-cookie|cookie)["']?\s*[:=]\s*)(\[SECRET\]|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n}\]]+)'''
)
_USERINFO = re.compile(r"(https?://[^\s/@:]+:)([^\s/@]+)(@)", re.I)
_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----.*?-----END \1-----", re.S)
_ADEPT = re.compile(
    r"(<(?:[\w.-]+:)?(?:privateKey|licenseKey|activationToken|pkcs12)\b[^>]*>).*?(</(?:[\w.-]+:)?(?:privateKey|licenseKey|activationToken|pkcs12)\s*>)",
    re.I | re.S,
)
REDACTED = "[SECRET]"


def protect_text(value: str) -> str:
    """Remove explicit credential values, never IDs, paths or ordinary URLs.

    Unlabelled arbitrary bytes cannot be identified as credentials. Producers
    must not concatenate such secrets into messages (handoff in #3632).
    """
    value = _PEM.sub(lambda m: f"-----BEGIN {m[1]}-----{REDACTED}-----END {m[1]}-----", value)
    value = _ADEPT.sub(lambda m: m[1] + REDACTED + m[2], value)
    value = _USERINFO.sub(lambda m: m[1] + REDACTED + m[3], value)
    value = _QUERY.sub(lambda m: m[1] + REDACTED, value)
    value = _COOKIE.sub(lambda m: m[1] + REDACTED, value)
    def replace(match):
        item = match["value"]
        quote = item[0] if item[:1] in {"'", '"'} else ""
        return match["prefix"] + quote + REDACTED + quote
    return _ASSIGNMENT.sub(replace, value)


def is_acquisition_failure(record: logging.LogRecord) -> bool:
    return (record.name == "cifereca" and record.levelno >= logging.WARNING
            and type(record.msg) is str
            and record.msg in {*_TEMPLATES, *_EXTRA, _INNER})


def _cause_fields(record: logging.LogRecord) -> dict:
    cause = record.exc_info
    if type(cause) is tuple and len(cause) == 3 and isinstance(cause[1], BaseException):
        try:
            return {"cause": {
                "type": type(cause[1]).__name__,
                "message": protect_text(str(cause[1])),
                "traceback": protect_text("".join(traceback.format_exception(*cause))),
            }, "cause_state": "observed"}
        except Exception:
            return {"cause_state": "unreadable"}
    if type(record.exc_text) is str and record.exc_text:
        return {"cause": {"traceback": protect_text(record.exc_text)}, "cause_state": "formatted_only"}
    return {"cause_state": "not_supplied"}


def _unreadable_payload(record: logging.LogRecord) -> dict:
    # Never fall back to formatting hostile args or appending unsanitized
    # exc_info just because an optional business identifier is malformed.
    payload = {
        "schema_version": 1, "category": "ACQUISITION",
        "operation": "loan_download_cause", "status": "unknown",
        "message": "Acquisition diagnostic arguments unavailable",
        "detail": {"arguments_state": "unreadable", **_cause_fields(record)},
    }
    reference = diagnostic_task_reference()
    if reference:
        payload["task_ref"] = reference
    return payload


def failure_payload(record: logging.LogRecord) -> dict | None:
    """Project ONLY values actually supplied on this physical occurrence."""
    if not is_acquisition_failure(record):
        return None
    args = record.args
    expected = len(_EXTRA[record.msg][3]) if record.msg in _EXTRA else 3 if "code=%s" in record.msg else 2
    if type(args) is not tuple or len(args) != expected:
        return _unreadable_payload(record)
    # No coercion of plugin-provided properties/objects into identifiers.
    kind = "acquisition_failure_cause"
    if record.msg in _EXTRA:
        status, phase, kind, names, code = _EXTRA[record.msg]
        if any(type(item) is not str and item is not None for item in args):
            return _unreadable_payload(record)
        detail = {key: protect_text(item) for key, item in zip(names, args) if type(item) is str}
        if code:
            detail["code"] = code
    elif record.msg == _INNER:
        if type(args[0]) is not str or type(args[1]) not in {float, int}:
            return _unreadable_payload(record)
        detail = {"loan_ref": protect_text(args[0]), "code": "provider_or_artifact_failure"}
        status, phase = "unknown", None
    else:
        if any(type(item) is not str and item is not None for item in args):
            return _unreadable_payload(record)
        detail = {key: protect_text(item) for key, item in zip(
            ("observed_provider_id", "loan_id", "code"), args,
        ) if type(item) is str}
        status, phase = _TEMPLATES[record.msg]
    detail["observation_kind"] = kind
    detail["observed_severity"] = record.levelname
    if phase:
        detail["phase"] = phase
    detail.update(_cause_fields(record))
    if type(record.stack_info) is str and record.stack_info:
        detail["stack_info"] = protect_text(record.stack_info)
    payload = {
        "schema_version": 1, "category": "ACQUISITION",
        "operation": "local_evidence_cause" if kind == "local_evidence_failure" else "loan_download_cause",
        "status": status,
        "message": protect_text(record.getMessage()), "detail": detail,
    }
    reference = diagnostic_task_reference()
    if reference:
        payload["task_ref"] = reference
        detail["task_ref"] = reference
    return payload
