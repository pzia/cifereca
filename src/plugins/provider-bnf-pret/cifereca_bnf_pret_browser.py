"""Ephemeral, user-opened Chromium reader session for the BnF portal.

No state files, credential arguments, HAR, recorder dependency or Core imports.
Call every method on the owning thread. Browser exceptions are never exposed.
"""
from __future__ import annotations

import secrets
import threading
from queue import Queue, Empty
from urllib.parse import parse_qsl, urlsplit, unquote

from cifereca_bnf_pret_web import (
    MAX_HTML_BYTES, MAX_NODES, MAX_DEPTH, ORIGIN, ProfileScope, WebParseError,
    _id, parse_detail, parse_loans, parse_suggestions,
)


class BrowserSessionError(ValueError):
    """Closed error code without browser errors, URLs or account information."""


def _reader_url(url):
    if type(url) is not str or len(url) > 4096 or any(ord(c) <= 32 or c == "\\" for c in url):
        return False
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "pret.bnf.fr" or parts.fragment:
            return False
        if _id(url, "resources"):
            return not parts.query
        if parts.path == "/my_profile/activity":
            return not parts.query
        if parts.path not in {"/", "/home", "/resources", "/results"}:
            return False
        query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
        keys = [key for key, _ in query]
        return (len(set(keys)) == len(keys)
                and set(keys) <= {"q", "availability", "sort_by", "page", "category", "category_standard"}
                and all(len(value) <= 2048 for _, value in query))
    except (ValueError, UnicodeError):
        return False


# Selection, size checks and sanitization happen inside the browser BEFORE HTML
# crosses the process boundary. Never page.content(), response.body(), cookies()
# or storage_state(). Form fields (including SAML) are never serialized.
EXTRACT_DOM = r"""({kind, maxBytes, maxNodes, maxDepth}) => {
  const fail = code => ({code});
  const selectors = {
    suggestions: '.search-results__list',
    detail: '[itemscope][itemtype~="https://schema.org/Book"], [itemscope][itemtype~="http://schema.org/Book"]',
    loans: '.active-loans'
  };
  if (!Object.prototype.hasOwnProperty.call(selectors, kind)) return fail('invalid_projection');
  const url = location.href;
  const roots = document.querySelectorAll(selectors[kind]);
  if (roots.length !== 1) return fail('unrecognized_dom');
  const tags = new Set(['article','section','div','ul','ol','li','a','span','p',
    'h1','h2','h3','b','strong','em','br','meta']);
  const classes = new Set(['search-results__list','search-resource-result',
    'active-loans','js-loan','lb_unavailable']);
  const properties = new Set(['name','author','publisher','isbn','inLanguage','description']);
  const encoder = new TextEncoder();
  let count = 0, bytes = 0;
  const charge = value => {
    if (value.length > maxBytes) throw 'html_limit';
    bytes += encoder.encode(value).length;
    if (bytes > maxBytes) throw 'html_limit';
    return value;
  };
  const esc = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
  const visit = (node, depth) => {
    if (++count > maxNodes || depth > maxDepth) throw 'dom_limit';
    if (node.nodeType === Node.TEXT_NODE) {
      // Loan identity only requires links. Ignore all loan/account text.
      if (kind === 'loans') return '';
      const value = node.textContent || '';
      if (value.length > maxBytes) throw 'html_limit';
      return charge(esc(value));
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    const tag = node.tagName.toLowerCase();
    if (['script','style','template','noscript','input','textarea','select','iframe'].includes(tag)
        || node.hidden || node.getAttribute('aria-hidden') === 'true') return '';
    const style = getComputedStyle(node);
    if (tag !== 'meta' && (style.display === 'none' || style.visibility === 'hidden')) return '';
    // Forms and unsupported wrappers are flattened, never serialize attributes.
    const outputTag = tags.has(tag) ? tag : 'div';
    let attrs = '';
    const cls = [...node.classList].filter(x => classes.has(x)).join(' ');
    if (cls) attrs += ' class="' + cls + '"';
    const prop = (node.getAttribute('itemprop') || '').split(/\s+/).filter(x => properties.has(x)).join(' ');
    if (prop && kind !== 'loans') attrs += ' itemprop="' + prop + '"';
    if (node.hasAttribute('itemscope') && kind !== 'loans') attrs += ' itemscope';
    const types = (node.getAttribute('itemtype') || '').split(/\s+/).filter(x =>
      /^https?:\/\/schema\.org\/(Book|Person|Organization)$/.test(x)).join(' ');
    if (types && kind !== 'loans') attrs += ' itemtype="' + types + '"';
    if (outputTag === 'meta' && prop && kind !== 'loans') {
      const content = node.getAttribute('content') || '';
      if (content.length > 4096) throw 'field_limit';
      attrs += ' content="' + esc(content) + '"';
    }
    if (tag === 'a') {
      const href = node.getAttribute('href') || '';
      if (href.length > 8192) throw 'field_limit';
      try {
        const u = new URL(href, location.href);
        if (u.origin === 'https://pret.bnf.fr' && !u.username && !u.password &&
            /^\/(resources|loans)\/[A-Za-z0-9_-]{1,128}\/?$/.test(u.pathname))
          attrs += ' href="' + u.pathname + '"';
      } catch (_) {}
    }
    const start = charge('<' + outputTag + attrs + '>');
    if (outputTag === 'meta' || outputTag === 'br') return start;
    let inner = '';
    for (const child of node.childNodes) inner += visit(child, depth + 1);
    return start + inner + charge('</' + outputTag + '>');
  };
  try {
    const html = visit(roots[0], 0);
    if (location.href !== url) return fail('page_changed');
    return {code:'ok', url, html};
  } catch (error) {
    return fail(['html_limit','dom_limit','field_limit'].includes(error) ? error : 'extraction_failed');
  }
}"""


class ReaderBrowserSession:
    """One ephemeral context for a locally selected identity/account.

    account_confirmed is a HUMAN declaration of the account used during SSO,
    not automatic verification of the upstream account identity or loan rights.
    No connected flag or Core rights are published by this adapter.
    """

    def __init__(self, identity_id, account_id, *, factory=None):
        self._scope = ProfileScope(identity_id, account_id, secrets.token_hex(16))
        self._factory = factory
        self._owner = threading.get_ident()
        self._runtime = self._browser = self._context = self._page = None
        self._mode = "new"
        self._generation = 0

    def __repr__(self):
        return f"ReaderBrowserSession(mode={self._mode!r})"

    @property
    def scope(self):
        return self._scope

    def _check(self, mode=None):
        if threading.get_ident() != self._owner:
            raise BrowserSessionError("wrong_thread")
        if mode is not None and self._mode != mode:
            raise BrowserSessionError("invalid_session_state")

    def open_login(self, *, consent=False):
        self._check("new")
        if consent is not True:
            raise BrowserSessionError("consent_required")
        try:
            if self._factory is None:
                from playwright.sync_api import sync_playwright
                manager = sync_playwright()
            else:
                manager = self._factory()
            self._runtime = manager.start()
            self._browser = self._runtime.chromium.launch(headless=False)
            self._context = self._browser.new_context(accept_downloads=False, service_workers="block")
            self._page = self._context.new_page()
            self._page.on("framenavigated", self._navigated)
            self._mode = "login"
            self._page.goto(ORIGIN + "/", wait_until="domcontentloaded", timeout=30000)
        except ImportError:
            self.close()
            raise BrowserSessionError("playwright_unavailable") from None
        except Exception:
            self.close()
            raise BrowserSessionError("browser_unavailable") from None

    def _navigated(self, frame):
        if self._page is not None and frame == self._page.main_frame:
            self._generation += 1

    def _route(self, route):
        try:
            request = route.request
            parts = urlsplit(request.url)
            path = unquote(parts.path).lower()
            forbidden = path.startswith(("/loans", "/v1")) or '%' in path or '\\' in path
            allowed = (request.method in {"GET", "HEAD"}
                       and parts.scheme == "https" and not forbidden)
            if request.is_navigation_request() or request.resource_type in {"fetch", "xhr"}:
                allowed = allowed and _reader_url(request.url)
            else:
                # No arbitrary GET subrequests (e.g. logout disguised as image).
                static = path.endswith(('.js', '.css', '.png', '.jpg', '.jpeg', '.gif',
                                        '.webp', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.otf'))
                allowed = allowed and static and request.resource_type in {"script", "stylesheet", "image", "font"}
            if allowed:
                route.continue_()
            else:
                route.abort("blockedbyclient")
        except Exception:
            try:
                route.abort("blockedbyclient")
            except Exception:
                pass

    def finish_login(self, *, account_confirmed=False):
        self._check("login")
        if account_confirmed is not True:
            raise BrowserSessionError("account_confirmation_required")
        try:
            # Apply to the entire context, including popups and subframes.
            self._context.route("**/*", self._route)
            self._context.route_web_socket("**/*", lambda socket: socket.close())
            self._mode = "readonly"
            return self.loans(expected=self.scope)
        except Exception:
            self.close()
            raise BrowserSessionError("login_not_verified") from None

    def _read(self, kind, expected):
        self._check("readonly")
        if not isinstance(expected, ProfileScope) or expected != self.scope:
            raise BrowserSessionError("scope_mismatch")
        generation = self._generation
        try:
            before = self._page.url
            if not _reader_url(before):
                raise BrowserSessionError("unexpected_page")
            result = self._page.evaluate(EXTRACT_DOM, {
                "kind": kind, "maxBytes": MAX_HTML_BYTES,
                "maxNodes": MAX_NODES, "maxDepth": MAX_DEPTH,
            })
            after = self._page.url
            if generation != self._generation or before != after:
                raise BrowserSessionError("page_changed")
            if not isinstance(result, dict) or result.get("code") != "ok":
                raise BrowserSessionError("unrecognized_dom")
            if result.get("url") != before:
                raise BrowserSessionError("page_changed")
            html = result.get("html")
            if kind == "loans":
                return parse_loans(html, page_url=before, expected=expected, observed=self.scope)
            if kind == "detail":
                return parse_detail(html, page_url=before)
            return parse_suggestions(html)
        except (BrowserSessionError, WebParseError):
            raise
        except Exception:
            raise BrowserSessionError("read_failed") from None

    def _goto(self, url, expected):
        self._check("readonly")
        if not isinstance(expected, ProfileScope) or expected != self.scope:
            raise BrowserSessionError("scope_mismatch")
        if not _reader_url(url):
            raise BrowserSessionError("unexpected_page")
        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = response.status if response is not None else None
        except Exception:
            raise BrowserSessionError("navigation_failed") from None
        if type(status) is not int or not 200 <= status < 300:
            code = {401: "auth_required", 403: "access_refused", 429: "rate_limited"}.get(status, "http_error")
            raise BrowserSessionError(code)
        if self._page.url != url:
            raise BrowserSessionError("unexpected_page")

    def loans(self, *, expected):
        self._goto(ORIGIN + "/my_profile/activity", expected)
        return self._read("loans", expected)

    def detail(self, resource_id, *, expected):
        if type(resource_id) is not str or _id("/resources/" + resource_id, "resources") != resource_id:
            raise BrowserSessionError("invalid_resource")
        self._goto(ORIGIN + "/resources/" + resource_id, expected)
        return self._read("detail", expected)

    def suggestions(self, *, expected):
        # Search via the visible website, no guessed autocomplete endpoint.
        return self._read("suggestions", expected)

    def pump(self):
        """For a worker/UI loop: process browser events on its owning thread."""
        self._check()
        if self._mode not in {"login", "readonly"}:
            raise BrowserSessionError("invalid_session_state")
        try:
            self._page.wait_for_timeout(100)
        except Exception:
            self.close()
            raise BrowserSessionError("session_closed") from None

    def close(self):
        self._check()
        self._mode = "closed"
        for name, method in (("_context", "close"), ("_browser", "close"), ("_runtime", "stop")):
            obj = getattr(self, name)
            setattr(self, name, None)
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        self._page = None


def _ask(session, prompt):
    """Keep Playwright callbacks moving while the operator types in stdin."""
    answers = Queue(maxsize=1)
    def collect():
        try:
            answers.put(input(prompt))
        except EOFError:
            answers.put(None)
    threading.Thread(target=collect, daemon=True).start()
    while True:
        try:
            value = answers.get_nowait()
            if value is None:
                raise EOFError
            return value.strip()
        except Empty:
            session.pump()


def main():
    """Manual local diagnostic entry point. No private values printed/saved."""
    session = ReaderBrowserSession("manual-local", "manual-local")
    try:
        if input("Ouvrir Chromium pour une session BnF Prêt éphémère ? [oui/non] ").strip() != "oui":
            return 0
        session.open_login(consent=True)
        if _ask(session, "Connectez-vous dans Chromium. Confirmez le compte choisi [oui/non] : ") != "oui":
            return 0
        snapshot = session.finish_login(account_confirmed=True)
        print("Prêts observés :", len(snapshot.loans), "État :", snapshot.status)
        while True:
            command = _ask(session, "Naviguez dans Chromium. [s] suggestions, [l] prêts, [q] quitter : ")
            if command == "q":
                break
            if command == "s":
                result = session.suggestions(expected=session.scope)
                print("Suggestions observées :", len(result.records), "État :", result.status)
            elif command == "l":
                result = session.loans(expected=session.scope)
                print("Prêts observés :", len(result.loans), "État :", result.status)
        return 0
    except (BrowserSessionError, WebParseError) as exc:
        print("BnF Prêt :", str(exc))
        return 1
    except (KeyboardInterrupt, EOFError):
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
