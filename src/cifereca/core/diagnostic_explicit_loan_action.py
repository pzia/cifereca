"""Provider-neutral, bounded terminal facts for explicitly consented loan actions.

Core records completed *attempts*, never rights, successful token transfers
or a book acquisition. Reason suffixes are closed and the provider prefix
is validated against exact typed provenance, not inferred from a log message.
"""
from __future__ import annotations

import json
import re
import time

from ..diagnostic_task_context import diagnostic_task_reference
from ..plugin_api import ProviderConfigurationError

ACTIONS = frozenset({"stage_loan_token", "import_acsm"})
PHASES = frozenset({
    "admission", "source_scope", "local_staging", "provider_delivery",
    "artifact_validation", "source_revalidation", "adept_import",
})
_STAGE_REASONS = frozenset({
    "explicit_confirmation_required", "loan_scope_unproven",
    "loan_token_unavailable", "loan_token_invalid", "acquisition_busy",
    "local_staging_failed", "action_failed",
    "loan_not_observed", "invalid_loan_token", "token_limit",
    "session_closed", "download_redirect_refused",
    "download_storage_failed", "auth_required", "access_denied",
    "rate_limited", "http_unavailable", "operation_timeout",
    "transport_unavailable", "invalid_activity_page", "unrecognized_loans",
    "ambiguous_loans", "unexpected_content", "invalid_dom", "html_limit",
    "scope_mismatch", "activity_access_denied", "activity_auth_required",
    "activity_rate_limited", "activity_http_unavailable",
    "activity_transport_unavailable", "activity_operation_timeout",
    "activity_url_refused", "activity_redirect_invalid",
    "activity_redirect_limit", "activity_unexpected_content",
    "activity_html_limit", "activity_invalid_dom", "activity_dom_limit",
    "activity_unrecognized_loans", "activity_ambiguous_loans",
    "activity_invalid_activity_page", "activity_scope_mismatch",
    "activity_unavailable",
})
_IMPORT_REASONS = frozenset({
    "loan_scope_unproven", "import_failed", "import_unconfirmed",
})
_STAGE_SUCCESS = frozenset({"candidate_acsm"})
_IMPORT_SUCCESS = frozenset({"complete", "unchanged"})
_IMPORT_ATTENTION = frozenset({"adept_profile_required", "adept_profile_ambiguous"})
_PROVIDER = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_TASK_REF = re.compile(r"[0-9a-f]{12}\Z")
_LOG = re.compile(r"EVENT ([a-z][a-z0-9-]{0,63}) loan action (.{1,650})\Z")


def _provider_prefix(provider_id):
    if type(provider_id) is not str or _PROVIDER.fullmatch(provider_id) is None:
        return None
    return provider_id.split("-", 1)[0]


def _known_code(code):
    if type(code) is not str or _CODE.fullmatch(code) is None:
        return False
    if code in (_STAGE_SUCCESS | _IMPORT_SUCCESS | _IMPORT_ATTENTION
                | {"loan_action_failed"}):
        return True
    return "_" in code and code.split("_", 1)[1] in (
        _STAGE_REASONS | _IMPORT_REASONS
    )


def valid_stage_code(code, provider_id):
    prefix = _provider_prefix(provider_id)
    return (prefix is not None and type(code) is str
            and _CODE.fullmatch(code) is not None
            and (code in _STAGE_SUCCESS
                 or (code.startswith(prefix + "_")
                     and code[len(prefix) + 1:] in _STAGE_REASONS)))


def _valid_reason(code, provider_id, action):
    prefix = _provider_prefix(provider_id)
    if prefix is None or type(code) is not str or _CODE.fullmatch(code) is None:
        return False
    if action == "stage_loan_token" and code in _STAGE_SUCCESS:
        return True
    if action == "import_acsm" and code in (_IMPORT_SUCCESS | _IMPORT_ATTENTION):
        return True
    reasons = _STAGE_REASONS if action == "stage_loan_token" else _IMPORT_REASONS
    return (code == "loan_action_failed"
            or (code.startswith(prefix + "_")
                and code[len(prefix) + 1:] in reasons))


class LoanActionError(ProviderConfigurationError):
    """Sanitized closed reason only; no plugin exception text."""
    def __init__(self, code):
        self.code = code if _known_code(code) else "loan_action_failed"
        super().__init__(self.code)


def public_fields(value):
    required = {
        "schema_version", "provider_id", "action", "phase", "status",
        "reason_code", "duration_ms",
    }
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) not in (required, required | {"task_ref"})):
        return {}
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or _provider_prefix(value["provider_id"]) is None
            or type(value["action"]) is not str or value["action"] not in ACTIONS
            or type(value["phase"]) is not str or value["phase"] not in PHASES
            or type(value["status"]) is not str
            or value["status"] not in {"success", "failed", "attention_required"}
            or not _valid_reason(value["reason_code"], value["provider_id"], value["action"])
            or type(value["duration_ms"]) is not int
            or not 0 <= value["duration_ms"] <= 86_400_000):
        return {}
    if ("task_ref" in value
            and (type(value["task_ref"]) is not str
                 or _TASK_REF.fullmatch(value["task_ref"]) is None)):
        return {}
    code = value["reason_code"]
    success = code in (_STAGE_SUCCESS | _IMPORT_SUCCESS)
    attention = code in _IMPORT_ATTENTION
    if value["status"] != (
        "success" if success else "attention_required" if attention else "failed"
    ):
        return {}
    if (value["action"] == "stage_loan_token" and value["phase"] == "adept_import"
            or value["action"] == "import_acsm"
            and value["phase"] not in {"admission", "adept_import"}):
        return {}
    return dict(value)


def _prefix(provider_id):
    prefix = _provider_prefix(provider_id)
    return "EVENT " + prefix + " loan action " if prefix is not None else ""


def log_message(fields):
    safe = public_fields(fields)
    return (
        _prefix(safe["provider_id"])
        + json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if safe else None
    )


def public_log_fields(line):
    if type(line) is not str or len(line) > 700:
        return {}
    match = _LOG.fullmatch(line)
    if match is None:
        return {}
    try:
        fields = public_fields(json.loads(match.group(2)))
    except (ValueError, TypeError, RecursionError):
        return {}
    return fields if fields and log_message(fields) == line else {}


def public_message(fields):
    return ("Prêt numérique · "
            + ("Récupération ACSM" if fields["action"] == "stage_loan_token"
               else "Import ADEPT")
            + " · " + fields["phase"] + " · " + fields["reason_code"])


class ActionObservation:
    """Exactly one terminal fact; caller owns admission and source identity."""

    def __init__(self, service, action, *, provider_id):
        self.service, self.action, self.provider_id = service, action, provider_id
        self.phase = "admission"
        self.started = time.monotonic()

    def at(self, phase):
        self.phase = phase

    def emit(self, status, code):
        try:
            fields = dict(
                schema_version=1, provider_id=self.provider_id,
                action=self.action, phase=self.phase, status=status,
                reason_code=code, duration_ms=min(
                    86_400_000, max(0, int((time.monotonic() - self.started) * 1000)),
                ),
            )
            task_ref = diagnostic_task_reference()
            if task_ref is not None:
                fields["task_ref"] = task_ref
            self.service.diagnostics.add_core_explicit_loan_action(fields)
        except Exception:
            pass  # Observability must never affect the business action.
