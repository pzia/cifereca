"""Privacy-first, opt-in Playwright recorder: no raw HAR or auth persistence.

The exported HAR is deliberately a *metadata-only* HAR 1.2 subset. Playwright's
record_har feature must never be used here: it writes credentials and payloads.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
from threading import Event, Semaphore
from urllib.parse import unquote, urlsplit

MAX_EVENTS = 300
MAX_BYTES = 256_000
SCHEMA = "cifereca.browser-recorder/1"
REDACTED = "~redacted~"
# Only these known, nonspecific URL components can leave the browser process.
# Account IDs, titles, tokens and arbitrary paths are hidden even if alphabetic.
SAFE_SEGMENTS = frozenset((
    "account", "auth", "bibliotheque", "book", "books", "catalog", "catalogue",
    "connexion", "dashboard", "emprunts", "home", "index.php", "library",
    "livres", "loan", "loans", "login", "my", "oauth", "profile", "search",
    "session", "sign-in", "signin", "v1", "v2",
))
# No logout, purchase, download, action, callback, password-reset or POST replay.
REPLAY_SEGMENTS = frozenset((
    "account", "bibliotheque", "book", "books", "catalog", "catalogue",
    "dashboard", "emprunts", "home", "index.php", "library", "livres",
    "loan", "loans", "my", "profile", "search", "v1", "v2",
))
FILENAME = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}[.]har$")


class RecorderError(Exception):
    """Stable and non-sensitive error identifier."""


def site_origin(url: str) -> str:
    if type(url) is not str or not 8 <= len(url) <= 2048:
        raise RecorderError("invalid_origin")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise RecorderError("invalid_origin")
    try:
        p = urlsplit(url)
        host = p.hostname
        if (p.scheme != "https" or not host or p.username is not None
                or p.password is not None or p.port not in (None, 443)):
            raise RecorderError("invalid_origin")
        if (host == "localhost" or host.endswith(".localhost")
                or not re.fullmatch(r"[a-zA-Z0-9.-]{2,253}", host)
                or ".." in host or host.startswith("-")):
            raise RecorderError("invalid_origin")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise RecorderError("invalid_origin")
        return "https://" + host.lower()
    except (ValueError, UnicodeError) as exc:
        raise RecorderError("invalid_origin") from None


def safe_url(url: str, origin: str) -> str | None:
    """Project an untrusted URL to an allowlisted *shape*, never its query."""
    try:
        if site_origin(url) != origin:
            return None
        p = urlsplit(url)
        segments = p.path.split("/")
        result = []
        for segment in segments:
            plain = unquote(segment)
            result.append(plain if plain in SAFE_SEGMENTS or not plain else REDACTED)
        # In particular: query, fragment, userinfo and raw path are NOT persisted.
        return origin + "/".join(result).replace("//", "/")
    except (RecorderError, ValueError, UnicodeError, TypeError):
        return None


def replayable(url: str, origin: str) -> bool:
    if type(url) is not str or safe_url(url, origin) != url:
        return False
    p = urlsplit(url)
    if p.query or p.fragment or REDACTED in p.path:
        return False
    return all(segment in REPLAY_SEGMENTS or segment == ""
               for segment in p.path.split("/"))


def _request_entry(method: str, url: str, started: str) -> dict:
    method = method if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
    return {
        "startedDateTime": started, "time": 0,
        "request": {
            "method": method, "url": url, "httpVersion": "",
            "cookies": [], "headers": [], "queryString": [],
            "headersSize": -1, "bodySize": -1,
        },
        "response": {
            "status": 0, "statusText": "", "httpVersion": "",
            "cookies": [], "headers": [],
            "content": {"size": 0, "mimeType": ""},
            "redirectURL": "", "headersSize": -1, "bodySize": -1,
        },
        "cache": {}, "timings": {"send": 0, "wait": 0, "receive": 0},
    }


class Capture:
    def __init__(self, origin: str, *, limit: int = MAX_EVENTS) -> None:
        self.origin = site_origin(origin)
        self.limit = max(1, min(MAX_EVENTS, int(limit)))
        self.entries: list[dict] = []
        self.navigations: list[str] = []
        self.dropped = 0
        self._pending: dict[int, dict] = {}
        self._next_exchange = 0

    def request(self, request) -> None:
        url = safe_url(request.url, self.origin)
        if url is None:
            return
        if len(self.entries) >= self.limit:
            self.dropped += 1
            return
        entry = _request_entry(str(request.method), url, datetime.now(timezone.utc).isoformat())
        self._next_exchange += 1
        entry["_cifereca"] = {"exchange_id": self._next_exchange, "outcome": "pending"}
        self.entries.append(entry)
        self._pending[id(request)] = entry

    def response(self, response) -> None:
        req = response.request
        entry = self._pending.pop(id(req), None)
        if entry is not None:
            try:
                status = int(response.status)
            except (ValueError, TypeError):
                status = 0
            entry["response"]["status"] = status if 100 <= status <= 599 else 0
            entry["_cifereca"]["outcome"] = (
                "redirect" if 300 <= status <= 399 else "response"
            )

    def request_failed(self, request) -> None:
        """Associate failures with requests, without inspecting error text.

        Playwright failure descriptions can contain raw URLs, cookies and
        provider data; persist only the fixed, nonsensitive outcome.
        """
        entry = self._pending.pop(id(request), None)
        if entry is not None:
            entry["_cifereca"]["outcome"] = "network_error"

    def navigate(self, url: str) -> None:
        shape = safe_url(url, self.origin)
        if shape is not None and len(self.navigations) < self.limit:
            if not self.navigations or self.navigations[-1] != shape:
                self.navigations.append(shape)

    def document(self) -> dict:
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "cifereca-browser-recorder", "version": "0.1.0"},
                "entries": self.entries,
                "_cifereca": {
                    "schema": SCHEMA, "origin": self.origin,
                    "navigations": self.navigations, "dropped": self.dropped,
                    "privacy": "metadata_only_no_headers_cookies_bodies_query_or_storage",
                },
            },
        }


def save_capture(directory: Path, capture: Capture) -> str:
    directory = Path(directory)
    if directory.is_symlink():
        raise RecorderError("unsafe_directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.is_dir():
        raise RecorderError("unsafe_directory")
    payload = json.dumps(capture.document(), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    if len(payload) > MAX_BYTES:
        raise RecorderError("capture_limit")
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".har"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(directory / name, flags, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
    except OSError:
        raise RecorderError("save_failed") from None
    return name


def load_capture(directory: Path, name: str, origin: str) -> list[str]:
    if type(name) is not str or not FILENAME.fullmatch(name):
        raise RecorderError("invalid_filename")
    directory = Path(directory)
    if directory.is_symlink():
        raise RecorderError("unsafe_directory")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(directory / name, flags)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise RecorderError("capture_limit")
            raw = source.read(MAX_BYTES + 1)
    except OSError:
        raise RecorderError("read_failed") from None
    if len(raw) > MAX_BYTES:
        raise RecorderError("capture_limit")
    try:
        doc = json.loads(raw)
        log = doc["log"]
        meta = log["_cifereca"]
        if meta["schema"] != SCHEMA or meta["origin"] != site_origin(origin):
            raise RecorderError("capture_origin_mismatch")
        paths = meta["navigations"]
        if not isinstance(paths, list) or len(paths) > MAX_EVENTS:
            raise RecorderError("invalid_capture")
        if not all(type(path) is str and len(path) <= 2048
                   and safe_url(path, meta["origin"]) == path for path in paths):
            raise RecorderError("invalid_capture")
    except (KeyError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise RecorderError("invalid_capture") from None
    return list(dict.fromkeys(path for path in paths if replayable(path, meta["origin"])))


def _playwright(factory=None):
    if factory is not None:
        return factory()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RecorderError("playwright_unavailable") from None
    return sync_playwright()


def record(start_url: str, directory: Path, stop: Event, *, save_requested: Event | None = None,
           status=None, factory=None) -> str:
    """Manual browser session: only save after the operator clicks Stop."""
    origin = site_origin(start_url)
    capture = Capture(origin)
    if safe_url(start_url, origin) is None:
        raise RecorderError("invalid_origin")
    with _playwright(factory) as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(accept_downloads=False, service_workers="block")
            try:
                context.on("request", capture.request)
                context.on("response", capture.response)
                context.on("requestfailed", capture.request_failed)

                def bind(page):
                    page.on("framenavigated", lambda frame: (
                        capture.navigate(frame.url) if frame == page.main_frame else None
                    ))

                context.on("page", bind)
                page = context.new_page()
                bind(page)
                page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                if status:
                    status("capture_ready")
                while not stop.is_set():
                    page.wait_for_timeout(200)
            finally:
                context.close()
        finally:
            browser.close()
    if not stop.is_set() or (save_requested is not None and not save_requested.is_set()):
        raise RecorderError("capture_interrupted")
    return save_capture(directory, capture)


def replay(directory: Path, name: str, origin: str, stop: Event, advance: Semaphore,
           *, status=None, factory=None) -> int:
    """Never automatically replay authentication, mutations or arbitrary URLs."""
    origin = site_origin(origin)
    steps = load_capture(directory, name, origin)
    with _playwright(factory) as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(accept_downloads=False, service_workers="block")
            try:
                page = context.new_page()
                page.goto(origin + "/", wait_until="domcontentloaded", timeout=30000)
                if status:
                    status("replay_ready")
                count = 0
                for path in steps:
                    while not stop.is_set() and not advance.acquire(timeout=0.2):
                        page.wait_for_timeout(150)
                    if stop.is_set():
                        break
                    # Revalidate immediately before each GET. No POST/forms/click replay.
                    if not replayable(path, origin):
                        raise RecorderError("unsafe_replay_step")
                    page.goto(path, wait_until="domcontentloaded", timeout=30000)
                    count += 1
                    if status:
                        status("replay_step")
                return count
            finally:
                context.close()
        finally:
            browser.close()

