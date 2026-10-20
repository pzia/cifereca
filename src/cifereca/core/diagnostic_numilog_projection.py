"""Closed Numilog producer schema at the Core public export boundary (#1884).

Unknown fields/values stay private. No provider parsing or plugin imports.
"""
from __future__ import annotations

import re

_SESSION_REASONS = frozenset({
    'connect_started', 'connect_succeeded', 'credentials_missing',
    'connect_failed', 'session_closed', 'not_connected',
    'library_redirect', 'library_invalid', 'library_http_error', 'unknown',
    'delivery_login_form_detected',
})
_DELIVERY_VALUES = {
    'source': frozenset({'readbook', 'owned_card', 'direct_url'}),
    'phase': frozenset({'start', 'delivery_http', 'html_selection',
                        'artifact_validation', 'adept_fulfillment', 'completed'}),
    'code': frozenset({
        'started', 'redirect', 'http_ok', 'single_allowed_candidate',
        'html_delivery_link_missing', 'html_delivery_ambiguous',
        'session_expired_confirmed', 'login_form_detected', 'html_error', 'html_invalid',
        'http_forbidden', 'http_unauthorized', 'http_rate_limited',
        'link_expired', 'http_error', 'origin_rejected', 'redirect_loop',
        'redirect_limit', 'mime_unexpected', 'download_oversize',
        'artifact_invalid', 'xml_not_acsm', 'adept_attention_required',
        'adept_unconfirmed', 'epub_complete', 'acsm_complete',
        'html_oversize', 'unclassified_error',
    }),
    'mime': frozenset({'html', 'acsm_xml', 'xml', 'epub', 'binary', 'other'}),
    'html_kind': frozenset({'login', 'error', 'intermediate', 'invalid', 'unknown'}),
    'origin_kind': frozenset({'acsm_fulfillment', 'numilog_site',
                             'numilog_service', 'eden', 'cdn', 'other'}),
    'path_kind': frozenset({'acsm_fulfillment', 'readbook', 'personal_library',
                            'login_page', 'confirmation_page', 'other'}),
    # Producer v0.1.41+ emits target enums only after the redirect URL
    # passes its own allowlist. The Core must never accept raw Location.
    'redirect_target_origin_kind': frozenset({'acsm_fulfillment', 'numilog_site',
                                               'numilog_service', 'eden', 'cdn', 'other'}),
    'redirect_target_kind': frozenset({'acsm_fulfillment', 'readbook',
                                       'personal_library', 'login_page',
                                       'confirmation_page', 'other'}),
}
_DELIVERY_BOOL_FIELDS = (
    'connected_initial', 'cookie_header_present',
    'confirmation_ref_present', 'confirmation_disp_simple',
    'redirect_confirmation_ref_present', 'redirect_confirmation_disp_simple',
)
_SESSION_LOG = re.compile(r'TRACE numilog session connected=(True|False) reason=([a-z_]+)')


def public_numilog_fields(operation: str, detail: object, duration: object = None) -> dict:
    """Allow only exact primitive types, fixed enums and bounded counters."""
    if type(detail) is not dict:
        return {}
    fields = {}
    if operation == 'acquisition_delivery':
        action = detail.get('action')
        if type(action) is str and re.fullmatch(r'[0-9a-f]{12}', action):
            fields['action'] = action
        for key, allowed in _DELIVERY_VALUES.items():
            value = detail.get(key)
            if type(value) is str and value in allowed:
                fields[key] = value
        for key, low, high in (('http_status', 100, 599), ('candidates', 0, 10_000), ('hops', 0, 12)):
            value = detail.get(key)
            if type(value) is int and low <= value <= high:
                fields[key] = value
        for key in _DELIVERY_BOOL_FIELDS:
            # A bool alone does not attest that the URL was a qualified
            # confirmation: preserve its meaning only with the matching
            # fixed route enum; never reconstruct a private order URL.
            if key.startswith('confirmation_') and fields.get('path_kind') != 'confirmation_page':
                continue
            if (key.startswith('redirect_confirmation_')
                    and fields.get('redirect_target_kind') != 'confirmation_page'):
                continue
            flag = detail.get(key)
            if type(flag) is bool:
                fields[key] = flag
        if type(duration) is int and 0 <= duration <= 600_000:
            fields['duration_ms'] = duration
    elif operation == 'startup_materialization':
        from .numilog_startup_diagnostics import public_fields
        fields = public_fields(detail)
    elif operation == 'session_state':
        reason = detail.get('reason')
        if type(reason) is str and reason in _SESSION_REASONS:
            fields['reason'] = reason
        connected = detail.get('connected')
        if type(connected) is bool:
            fields['connected'] = connected
    return fields


def public_numilog_message(operation: str, fields: dict) -> str | None:
    if operation not in {'acquisition_delivery', 'session_state', 'startup_materialization'}:
        return None
    # The message survives existing UI/selected-event exports (500 chars).
    pairs = sorted(fields.items()) if operation == 'startup_materialization' else fields.items()
    return (operation + ''.join(f' {key}={value}' for key, value in pairs))[:500]


def public_numilog_session_fields(body: object) -> dict:
    """Decode only the exact closed session trace from the Numilog producer.

    A matching prefix plus arbitrary account details is not evidence. This
    merely projects a plugin-emitted observation; it does not attest a Core
    operation, an account, or any link to another task.
    """
    if type(body) is not str or len(body) > 200:
        return {}
    match = _SESSION_LOG.fullmatch(body)
    if match is None or match[2] not in _SESSION_REASONS:
        return {}
    return {'connected': match[1] == 'True', 'reason': match[2]}


def public_numilog_session_log(body: str) -> str | None:
    """Accept the entire fixed producer trace, never a matching substring."""
    fields = public_numilog_session_fields(body)
    if not fields:
        return None
    return (
        f"TRACE numilog session connected={fields['connected']}"
        f" reason={fields['reason']}"
    )
