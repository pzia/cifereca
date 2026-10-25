"""Closed terminal facts for the two consented BnF ACSM actions.

An action here proves an attempt, never an entitlement, a successful HTTP
request, or a new loan. Provider reasons are finite observations, not logs.
"""
from __future__ import annotations
import json
import re
import time

from ..diagnostic_task_context import diagnostic_task_reference
from ..plugin_api import ProviderConfigurationError, ProviderError

PREFIX = 'EVENT bnf loan action '
ACTIONS = frozenset({'stage_loan_token', 'import_acsm'})
PHASES = frozenset({'admission', 'source_scope', 'local_staging', 'provider_delivery',
                    'artifact_validation', 'source_revalidation', 'adept_import'})
STAGE_CODES = frozenset({
    'bnf_explicit_confirmation_required', 'bnf_loan_scope_unproven',
    'bnf_loan_token_unavailable', 'bnf_loan_token_invalid', 'bnf_acquisition_busy',
    'bnf_local_staging_failed', 'bnf_action_failed', 'candidate_acsm',
})
PROVIDER_REASONS = frozenset({
    'loan_not_observed', 'invalid_loan_token', 'token_limit', 'session_closed',
    'download_redirect_refused', 'download_storage_failed', 'auth_required',
    'access_denied', 'rate_limited', 'http_unavailable', 'operation_timeout',
    'transport_unavailable', 'invalid_activity_page', 'unrecognized_loans',
    'ambiguous_loans', 'unexpected_content', 'invalid_dom', 'html_limit',
    'scope_mismatch',
})
STAGE_CODES |= frozenset('bnf_' + reason for reason in PROVIDER_REASONS)
IMPORT_CODES = frozenset({'complete', 'unchanged', 'adept_profile_required',
                         'adept_profile_ambiguous', 'bnf_loan_scope_unproven',
                         'bnf_import_failed', 'bnf_import_unconfirmed'})


class BnfLoanActionError(ProviderConfigurationError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in STAGE_CODES | IMPORT_CODES else 'bnf_action_failed'
        super().__init__(self.code)


def provider_reason(error):
    # Never str(error), class names, arbitrary attributes, or log parsing.
    if not isinstance(error, ProviderError):
        return 'bnf_loan_token_unavailable'
    try:
        args = error.args
        if type(args) is tuple and len(args) == 1 and type(args[0]) is str and args[0] in PROVIDER_REASONS:
            return 'bnf_' + args[0]
    except Exception:
        pass
    return 'bnf_loan_token_unavailable'


def public_fields(value):
    required = {'schema_version', 'provider_id', 'action', 'phase', 'status', 'reason_code', 'duration_ms'}
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) not in (required, required | {'task_ref'})):
        return {}
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['provider_id']) is not str or value['provider_id'] != 'bnf-pret'
            or type(value['action']) is not str or value['action'] not in ACTIONS
            or type(value['phase']) is not str or value['phase'] not in PHASES
            or type(value['status']) is not str or value['status'] not in {'success','failed','attention_required'}
            or type(value['reason_code']) is not str
            or value['reason_code'] not in (STAGE_CODES if value['action']=='stage_loan_token' else IMPORT_CODES)
            or type(value['duration_ms']) is not int or not 0 <= value['duration_ms'] <= 86_400_000):
        return {}
    if 'task_ref' in value and (type(value['task_ref']) is not str or not re.fullmatch('[0-9a-f]{12}', value['task_ref'])):
        return {}
    success = value['reason_code'] in {'candidate_acsm','complete','unchanged'}
    attention = value['reason_code'] in {'adept_profile_required','adept_profile_ambiguous'}
    if value['status'] != ('success' if success else 'attention_required' if attention else 'failed'):
        return {}
    if value['action']=='stage_loan_token' and value['phase']=='adept_import':
        return {}
    if value['action']=='import_acsm' and value['phase'] not in {'admission','adept_import'}:
        return {}
    return dict(value)


def log_message(fields):
    safe = public_fields(fields)
    return PREFIX + json.dumps(safe,sort_keys=True,separators=(',',':'),ensure_ascii=True) if safe else None


def public_log_fields(line):
    if type(line) is not str or len(line)>700 or not line.startswith(PREFIX):
        return {}
    try:
        fields = public_fields(json.loads(line[len(PREFIX):]))
    except (ValueError,TypeError,RecursionError):
        return {}
    return fields if fields and log_message(fields)==line else {}


def public_message(fields):
    return ('BnF Prêt · ' + ('Récupération ACSM' if fields['action']=='stage_loan_token' else 'Import ADEPT')
            + ' · ' + fields['phase'] + ' · ' + fields['reason_code'])


class ActionObservation:
    """Exactly one terminal event, using only phases actually entered by Core."""
    def __init__(self, service, action):
        self.service, self.action, self.phase = service, action, 'admission'
        self.started = time.monotonic()

    def at(self, phase):
        self.phase = phase

    def emit(self, status, code):
        try:
            fields = dict(schema_version=1,provider_id='bnf-pret',action=self.action,
                          phase=self.phase,status=status,reason_code=code,
                          duration_ms=min(86_400_000,max(0,int((time.monotonic()-self.started)*1000))))
            task_ref = diagnostic_task_reference()
            if task_ref is not None:
                fields['task_ref'] = task_ref
            self.service.diagnostics.add_core_bnf_loan_action(fields)
        except Exception:
            pass  # Optional observability must never change the action result.
