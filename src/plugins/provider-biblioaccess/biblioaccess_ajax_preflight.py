"""Offline evidence of dynamic AJAX-login dependencies for La Rochelle (#3691).

This parser reports only structure of a fresh institutional login HTML page:
same-origin script paths, field names, and explicit script references to the
observed AJAX keys. It never executes JavaScript, reads field values, solves a
captcha, calls the network, or invents a value for _dc/captchaCheckValue.

The output is an *unverified* preflight observation, not an authentication
contract. A service may load the login dynamically without exposing any of
these names in the initial HTML.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import re

IDP = "https://mediatheques.agglo-larochelle.fr"
_LOGIN_PATH = "/login"
MAX_HTML = 256 * 1024
MAX_NODES = 4096
MAX_SCRIPTS = 32
MAX_FIELDS = 64
MAX_INLINE = 128 * 1024
_NAMES = frozenset(("_dc", "captchaCheckValue", "captcha", "changeUser", "provider"))
_SAFE_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9.:-]{0,63}\Z")


class LoginPreflightError(ValueError):
    """Closed non-sensitive error identifiers."""


@dataclass(frozen=True)
class LoginPreflight:
    """No script source URL, form value, login or password is returned."""

    script_count: int
    same_origin_script_paths: tuple[str, ...]
    external_script_count: int
    inline_script_count: int
    field_names: tuple[str, ...]
    inline_identifier_names: tuple[str, ...]
    status: str = "unverified"
    values_extracted: bool = False
    challenge_resolved: bool = False


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = 0
        self.depth = 0
        self.script_depth = 0
        self.inline_size = 0
        self.inline_chunks = []
        self.scripts = []
        self.fields = []
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_NODES or len(attrs) > 64:
            raise LoginPreflightError("login_preflight_limit")
        if len({key for key, _ in attrs}) != len(attrs):
            raise LoginPreflightError("login_preflight_invalid_html")
        values = dict(attrs)
        if tag == "script":
            self.depth += 1
            if self.depth > 1 or len(self.scripts) >= MAX_SCRIPTS:
                raise LoginPreflightError("login_preflight_limit")
            self.script_depth = 1
            self.scripts.append(values.get("src") or "")
        elif tag == "input":
            name = values.get("name") or ""
            if name and _SAFE_FIELD_NAME.fullmatch(name):
                if len(self.fields) >= MAX_FIELDS:
                    raise LoginPreflightError("login_preflight_limit")
                self.fields.append(name)

    def handle_endtag(self, tag):
        if tag == "script":
            self.depth = max(0, self.depth - 1)
            self.script_depth = 0

    def handle_data(self, data):
        if self.script_depth:
            self.inline_size += len(data)
            if self.inline_size > MAX_INLINE:
                raise LoginPreflightError("login_preflight_limit")
            # The HTML stays in this ephemeral parser; never return snippets.
            self.inline_chunks.append(data)


def _script_path(raw: str) -> str | None:
    if (type(raw) is not str or not raw or len(raw) > 4096
            or any(ord(ch) <= 32 or ch == "\\" for ch in raw)):
        return None
    try:
        target = urljoin(IDP + _LOGIN_PATH, raw)
        p = urlsplit(target)
        if (p.scheme != "https" or p.netloc != "mediatheques.agglo-larochelle.fr"
                or not p.path.endswith(".js") or p.fragment
                or p.username is not None or p.password is not None):
            return None
        # Query values may embed dynamic IDs or tokens; path is enough for
        # offline inventory. Script paths are bounded and not auto-fetched.
        if not 1 <= len(p.path) <= 512:
            return None
        return p.path
    except (TypeError, ValueError):
        return None


def inspect_login_preflight(html: str, *, page_url: str = IDP + _LOGIN_PATH) -> LoginPreflight:
    """Bounded syntactic reconnaissance of a fresh login response (offline)."""
    if page_url != IDP + _LOGIN_PATH and not (
        type(page_url) is str
        and page_url.startswith(IDP + _LOGIN_PATH + "?service=")
        and len(page_url) <= 4096
    ):
        raise LoginPreflightError("login_preflight_url_refused")
    if (type(html) is not str or len(html) > MAX_HTML
            or len(html.encode("utf-8")) > MAX_HTML):
        raise LoginPreflightError("login_preflight_limit")
    page = _Page()
    try:
        page.feed(html)
        page.close()
    except LoginPreflightError:
        raise
    except (ValueError, UnicodeError):
        raise LoginPreflightError("login_preflight_invalid_html") from None
    if page.depth:
        raise LoginPreflightError("login_preflight_invalid_html")
    paths = tuple(dict.fromkeys(
        p for raw in page.scripts if (p := _script_path(raw)) is not None
    ))
    external = sum(bool(raw) for raw in page.scripts)
    inline_text = "\n".join(page.inline_chunks)
    identifiers = tuple(sorted(
        name for name in _NAMES
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name)
                     + r"(?![A-Za-z0-9_])", inline_text)
    ))
    return LoginPreflight(
        script_count=len(page.scripts),
        same_origin_script_paths=paths,
        external_script_count=external,
        inline_script_count=len(page.scripts) - external,
        field_names=tuple(dict.fromkeys(page.fields)),
        inline_identifier_names=identifiers,
    )


@dataclass(frozen=True)
class JavascriptEvidence:
    identifier_names: tuple[str, ...]
    ajax_endpoint_referenced: bool
    source_type: str = "same_origin_script"
    executable: bool = False
    value_provenance_verified: bool = False


def inspect_javascript_evidence(source: str) -> JavascriptEvidence:
    """Offline, value-free structural indicators of an observed static JS.

    Lexical matches cannot prove the algorithm or the provenance of captcha
    values; scripts are never executed, serialized, returned or cached.
    """
    if (type(source) is not str or len(source) > MAX_INLINE
            or len(source.encode("utf-8")) > MAX_INLINE):
        raise LoginPreflightError("login_preflight_limit")
    identifiers = tuple(sorted(
        name for name in _NAMES
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(name)
                     + r"(?![A-Za-z0-9_])", source)
    ))
    return JavascriptEvidence(
        identifier_names=identifiers,
        ajax_endpoint_referenced="/logon.svc/logon" in source,
    )
