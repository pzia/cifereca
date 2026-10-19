"""Public failure observations only; ledger/UI error evidence stays unchanged."""
from __future__ import annotations

import re

from .loan_safe_trace import trace_integer

# Observed machine codes, not a licensing diagnosis inferred from raw text.
_CODES = frozenset({
    'kobo_adept_invalid_json', 'kobo_adept_invalid_utf8', 'kobo_adept_timeout',
    'kobo_adept_failed', 'kobo_adept_missing', 'kobo_adept_launch_error',
    'kobo_adept_invalid_command', 'kobo_adept_invalid_contract',
    'kobo_adept_contract_old', 'kobo_adept_invalid_response',
    'kobo_adept_output_too_large', 'kobo_adept_destination_mismatch',
    'kobo_adept_drm_mutation_unsupported', 'kobo_adept_invalid_loan_inventory',
    'adept_error', 'pnb_redirect_refused', 'runtime_refused',
})


def failure_fields(*, code, process, loan_ref):
    """Exact types, closed codes, bounded process facts; no exception text."""
    detail = {'phase': 'adept_fulfillment',
              'code': code if type(code) is str and code in _CODES else 'unclassified_error'}
    if type(loan_ref) is str and re.fullmatch('[0-9a-f]{20}', loan_ref):
        detail['loan_ref'] = loan_ref
    if type(process) is not dict:
        return detail
    for key, low, high in (('returncode', -255, 255), ('duration_ms', 0, 86_400_000),
                          ('stdout_bytes', 0, 2**63-1), ('stderr_bytes', 0, 2**63-1),
                          ('artifact_bytes', 0, 2**63-1)):
        value = trace_integer(process.get(key), minimum=low, maximum=high)
        if value is not None:
            detail[key] = value
    if type(process.get('artifact_present')) is bool:
        detail['artifact_present'] = process['artifact_present']
    return detail
