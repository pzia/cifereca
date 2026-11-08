"""Observed La Rochelle institutional AJAX logon contract (#3691).

Derived from one user-provided HAR of a POST to /logon.svc/logon.
The HAR itself, response payload, cookie and authentication values are NEVER
stored here. This is an HTTP-only, strictly opt-in protocol qualification;
a JSON success is not CAS completion or a connected Core provider session.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urlencode

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

ORIGIN = "https://mediatheques.agglo-larochelle.fr"
LOGON_PATH = "/logon.svc/logon"
QUERY_KEYS = frozenset(("changeUser", "provider", "_dc"))
FORM_KEYS = frozenset((
    "username", "password", "rememberMe", "captcha", "captchaCheckValue",
))
_RESPONSE_KEYS = frozenset(("errors", "message", "success", "d"))
MAX_RESPONSE = 65536


class AjaxLogonError(ValueError):
    """Stable status identifiers only; never include raw remote values."""


@dataclass(frozen=True)
class AjaxLogonObservation:
    status: int
    media_type: str
    outcome: str  # 'success_reported', 'failure_reported', 'unknown'
    cas_completed: bool = False
    identity_verified: bool = False
    core_connected: bool = False


def _validate_params(query: dict[str, str], fields: dict[str, str]) -> None:
    if (type(query) is not dict or set(query) != QUERY_KEYS
            or type(fields) is not dict or set(fields) != FORM_KEYS):
        raise AjaxLogonError("ajax_logon_shape_mismatch")
    for key, value in (*query.items(), *fields.items()):
        if (type(key) is not str or type(value) is not str
                or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise AjaxLogonError("ajax_logon_shape_mismatch")
    if not fields["username"] or not fields["password"]:
        raise AjaxLogonError("ajax_logon_credentials_missing")
    # The observed HAR proves the *field name*, not that its value must be
    # nonempty. Sending an explicit empty value is not a captcha bypass;
    # only the institutional server decides whether it accepts that value.


def inspect_ajax_response(*, status: int, mime: str, data: bytes) -> AjaxLogonObservation:
    if (type(status) is not int or status not in range(100, 600)
            or type(mime) is not str or len(mime) > 256
            or type(data) is not bytes or len(data) > MAX_RESPONSE):
        raise AjaxLogonError("ajax_logon_response_invalid")
    media = mime.split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise AjaxLogonError("ajax_logon_response_type")
    try:
        document = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise AjaxLogonError("ajax_logon_json_invalid") from None
    if type(document) is not dict or not set(document) <= _RESPONSE_KEYS:
        raise AjaxLogonError("ajax_logon_json_shape")
    # Field d may carry an opaque session continuation value. Never expose it.
    reported = document.get("success")
    if type(reported) is not bool:
        outcome = "unknown"
    elif reported and status == 200:
        outcome = "success_reported"
    else:
        outcome = "failure_reported"
    return AjaxLogonObservation(status=status, media_type=media, outcome=outcome)


class ObservedAjaxLogonClient:
    """Single-attempt direct HTTP; must be closed after each qualification."""

    def __init__(self, *, transport=None):
        from threading import Lock
        self._lock = Lock()
        self._attempted = False
        self._closed = False
        self.http = HTTPX_TRANSPORT.client(
            producer="provider.biblioaccess",
            timeout=httpx.Timeout(12, connect=8),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def submit_once(self, *, query: dict[str, str], fields: dict[str, str],
                    consent: bool) -> AjaxLogonObservation:
        if consent is not True:
            raise AjaxLogonError("ajax_logon_consent_required")
        _validate_params(query, fields)
        with self._lock:
            if self._closed:
                raise AjaxLogonError("ajax_logon_client_closed")
            if self._attempted:
                raise AjaxLogonError("ajax_logon_already_attempted")
            self._attempted = True
        try:
            with self.http.stream(
                "POST", ORIGIN + LOGON_PATH, params=query, content=urlencode(fields).encode("ascii"),
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": ORIGIN,
                },
                follow_redirects=False,
            ) as response:
                payload = bytearray()
                for piece in response.iter_bytes():
                    if len(payload) + len(piece) > MAX_RESPONSE:
                        raise AjaxLogonError("ajax_logon_response_limit")
                    payload.extend(piece)
                return inspect_ajax_response(
                    status=response.status_code,
                    mime=response.headers.get("content-type", ""),
                    data=bytes(payload))
        except (httpx.HTTPError, UnicodeError):
            raise AjaxLogonError("ajax_logon_outcome_unknown") from None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.http.cookies.clear()
        self.http.close()
