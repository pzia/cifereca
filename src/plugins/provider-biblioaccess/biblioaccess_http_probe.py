"""Explicit HTTPX SSO POST qualification, no browser and no automatic login.

Uses the public transport facade. A caller must affirmatively provide a
candidate field mapping built from a fresh form and user-supplied credentials.
No provider session is marked connected; a POST 200 is unverified evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

from biblioaccess_http_sso import (
    ORIGIN, LOGON_PATH, FormShape, SsoProtocolError,
    inspect_login_forms, logon_response_shape,
)

_MAX_RESPONSE_BYTES = 65536
_ALLOWED_CONTENT_TYPES = frozenset((
    "application/json", "text/html", "application/xhtml+xml",
))


@dataclass(frozen=True)
class LogonObservation:
    status: int
    media_type: str
    body_bytes: int
    authentication_verified: bool = False
    cas_completed: bool = False
    post_sent: bool = True


class BiblioAccessHTTPQualification:
    """Ephemeral HTTP-only client, caller owns shutdown and cancellation."""

    def __init__(self, *, transport=None):
        self.http = HTTPX_TRANSPORT.client(
            producer="provider.biblioaccess",
            timeout=httpx.Timeout(12, connect=8),
            follow_redirects=False, trust_env=False, transport=transport,
        )
        self._closed = False

    def close(self):
        if not self._closed:
            self._closed = True
            self.http.cookies.clear()
            self.http.close()

    def inspect_login(self) -> tuple[FormShape, ...]:
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        try:
            with self.http.stream("GET", ORIGIN + "/login",
                                  headers={"Accept": "text/html"},
                                  follow_redirects=False) as response:
                if response.status_code != 200:
                    raise SsoProtocolError("sso_login_unavailable")
                media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media not in ("text/html", "application/xhtml+xml"):
                    raise SsoProtocolError("sso_unexpected_content")
                body = response.read()
                if len(body) > 256000:
                    raise SsoProtocolError("sso_html_limit")
                return inspect_login_forms(
                    ORIGIN + "/login", body.decode("utf-8", errors="strict"))
        except (httpx.HTTPError, UnicodeError):
            raise SsoProtocolError("sso_transport_unavailable") from None

    def submit_once(
        self, *, shape: FormShape, fields: dict[str, str],
        explicit_consent: bool,
    ) -> LogonObservation:
        """One unverified POST, never a retry or a Core authentication."""
        if self._closed:
            raise SsoProtocolError("sso_transport_closed")
        if explicit_consent is not True:
            raise SsoProtocolError("sso_consent_required")
        if (type(shape) is not FormShape or shape.method != "POST"
                or shape.action_origin != ORIGIN
                or shape.action_path != LOGON_PATH
                or type(fields) is not dict
                or set(fields) != set(shape.field_names)
                or any(type(k) is not str or type(v) is not str or len(v) > 8192
                       for k, v in fields.items())
                or not shape.password_field_present):
            raise SsoProtocolError("sso_post_schema_unqualified")
        try:
            with self.http.stream(
                "POST", ORIGIN + LOGON_PATH, data=fields,
                headers={"Accept": "application/json, text/html"},
                follow_redirects=False,
            ) as response:
                body = response.read()
                observed = logon_response_shape(
                    status=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    body=body,
                )
                return LogonObservation(
                    status=observed["status"],
                    media_type=observed["media_type"],
                    body_bytes=observed["body_bytes"],
                )
        except (httpx.HTTPError, UnicodeError):
            # A POST could already have reached the server. No implicit retry.
            raise SsoProtocolError("sso_post_outcome_unknown") from None
