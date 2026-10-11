"""CifericaOPDS: static, offline-first experimental provider.collection pilot.

The real upstream OpenAPI v0.2 is not available: candidate_v02 routes and JSON
are explicitly NON-NORMATIVE. No network access at activate()/snapshot().
Only cifereca.plugin_api is imported; no private registry access or remote code.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import re
import json
import os
import stat
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cifereca.plugin_api import (
    ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderSession,
    SearchResult, ProviderCollectionSnapshot, validate_provider_collection_snapshot,
)

PARENT_ID = "org.cifereca.integration.cifericaopds"
MAX_JSON_BYTES = 1024 * 1024
MAX_RESULTS = 500


class ContractUnavailable(RuntimeError):
    """Unavailable, invalid or unverified server contract."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ContractUnavailable("redirect refused")


def safe_base_url(raw: str) -> str:
    """Reject credentials, fragments, arbitrary paths and plaintext off loopback."""
    try:
        parts = urlsplit(str(raw).strip())
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ("", "/")):
            raise ValueError("invalid origin")
        port = parts.port
        hostname = parts.hostname.lower()
    except (ValueError, TypeError) as exc:
        raise ContractUnavailable("invalid configured origin") from exc
    if parts.scheme == "http" and hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ContractUnavailable("HTTP is allowed only on loopback")
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit((parts.scheme, host + (f":{port}" if port is not None else ""), "", "", ""))


class JsonClient:
    def __init__(self, base_url, timeout=4):
        self.base_url = safe_base_url(base_url)
        self.timeout = min(20.0, max(0.5, float(timeout)))
        self._opener = build_opener(NoRedirects())

    def get(self, endpoint: str, *, params=None):
        if not endpoint.startswith("/api/v1/") or "?" in endpoint or "#" in endpoint:
            raise ContractUnavailable("endpoint not allowed")
        url = self.base_url + endpoint + ("?" + urlencode(params) if params else "")
        request = Request(url, headers={
            "Accept": "application/json", "User-Agent": "cifereca-cifericaopds/0.1"
        })
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise ContractUnavailable("unexpected HTTP status")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
                if content_type != "application/json":
                    raise ContractUnavailable("invalid JSON content type")
                raw = response.read(MAX_JSON_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise ContractUnavailable("server unavailable") from exc
        if len(raw) > MAX_JSON_BYTES:
            raise ContractUnavailable("oversized JSON response")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ContractUnavailable("invalid server JSON") from exc
        if not isinstance(result, dict):
            raise ContractUnavailable("JSON object required")
        return result


def _read_local_json(path: Path, *, label: str, optional: bool = False):
    """Bounded, no-symlink JSON read; never include local paths in failures.

    The file size is checked on the opened descriptor, not with a separate
    path.stat(), to prevent accidental unbounded reads after a file swap.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if optional:
            return None
        raise ContractUnavailable(f"missing local {label} fixture") from None
    except OSError:
        raise ContractUnavailable(f"unreadable local {label} JSON") from None
    try:
        with os.fdopen(fd, "rb") as handle:
            file_status = os.fstat(handle.fileno())
            if not stat.S_ISREG(file_status.st_mode):
                raise ContractUnavailable(f"invalid local {label} file")
            if file_status.st_size > MAX_JSON_BYTES:
                raise ContractUnavailable(f"oversized local {label} JSON")
            raw = handle.read(MAX_JSON_BYTES + 1)
    except OSError:
        raise ContractUnavailable(f"unreadable local {label} JSON") from None
    if len(raw) > MAX_JSON_BYTES:
        raise ContractUnavailable(f"oversized local {label} JSON")
    try:
        # Reject non-standard float literals rather than silently accepting NaN.
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ContractUnavailable(f"invalid local {label} JSON") from None


def _text(value, label, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractUnavailable(f"invalid {label}")
    return value.strip()


def candidate_inventory(data, previous, *, show_synthetic=False):
    """Candidate *fixture* schema; Core validates again before publication."""
    major, generation = data.get("api_major"), data.get("discovery_generation")
    if type(major) is not int or major != 1:
        raise ContractUnavailable("unsupported candidate API major")
    if type(generation) is not int or generation < 0:
        raise ContractUnavailable("invalid candidate discovery generation")
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) > 128:
        raise ContractUnavailable("invalid source list")
    children = []
    for source in sources:
        if not isinstance(source, dict):
            raise ContractUnavailable("invalid source")
        sid = _text(source.get("source_id"), "source_id", 64)
        kind = _text(source.get("kind"), "kind", 128)
        if kind == "synthetic.merged" and not show_synthetic:
            continue
        caps = source.get("capabilities")
        if not isinstance(caps, list) or any(not isinstance(x, str) for x in caps):
            raise ContractUnavailable("invalid capabilities")
        # No assumption of downloads, loans, authentication or SSE support.
        safe_caps = [x for x in caps if x in ("catalog.search", "metadata.enrich")]
        status = _text(source.get("status"), "status", 32)
        if status not in ("ready", "disabled", "removed", "degraded"):
            raise ContractUnavailable("unsupported source status")
        revision = _text(source.get("source_revision", source.get("revision")),
                         "source_revision", 128)
        catalog = source.get("catalog_generation", generation)
        if type(catalog) is not int or catalog < 0:
            raise ContractUnavailable("invalid catalog generation")
        children.append({
            "plugin_id": f"cifericaopds.{sid}", "source_id": sid,
            "capabilities": safe_caps, "source_revision": revision,
            "catalog_generation": catalog, "status": status,
            "kind": kind, "instantiation": "singleton_public",
            "parent_plugin_id": PARENT_ID, "is_virtual": True,
        })
    return validate_provider_collection_snapshot({
        "api_major": 1, "discovery_generation": generation,
        "parent_plugin_id": PARENT_ID, "children": children,
    }, parent_plugin_id=PARENT_ID, previous=previous)



def _published_text(value, *, maximum=256):
    """Allow only bounded scalar publisher facts, never URLs or credentials."""
    if not isinstance(value, str):
        return None
    # Check the raw value *before* whitespace normalization. Otherwise a CR,
    # newline or tab in upstream text can be silently converted into content.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        return None
    if re.search(r"(?:https?://|www\.|@|(?:access[_-]?token|api[_-]?key|password|bearer)\s*[:=])", cleaned, re.I):
        return None
    return cleaned


def _published_date(value):
    raw = _published_text(value, maximum=10)
    if raw is None or not re.fullmatch(r"(?:1[89]|20)\d{2}(?:-\d{2}-\d{2})?", raw):
        return None
    if len(raw) == 10:
        try:
            date.fromisoformat(raw)
        except ValueError:
            return None
    return raw


def _published_isbn(value):
    if not isinstance(value, str):
        return None
    raw = re.sub(r"[-\s]", "", value)
    if len(raw) != 13 or not raw.isdigit() or not raw.startswith(("978", "979")):
        return None
    checksum = sum(int(number) * (1 if index % 2 == 0 else 3)
                   for index, number in enumerate(raw))
    return raw if checksum % 10 == 0 else None


def candidate_results(data, provider_id):
    """Conservative projection: no inferred rights, signed URLs or user data."""
    if not isinstance(data, dict):
        raise ContractUnavailable("invalid candidate result envelope")
    values = data.get("items")
    if not isinstance(values, list) or len(values) > MAX_RESULTS:
        raise ContractUnavailable("invalid candidate item list")
    rows, seen = [], set()
    for item in values:
        if not isinstance(item, dict):
            raise ContractUnavailable("invalid candidate item")
        # Opaque but path-free IDs only; reject URLs, tokens and arbitrary
        # serialized objects before they reach SearchResult or UI/log contexts.
        record_id = item.get("id")
        if not isinstance(record_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", record_id):
            raise ContractUnavailable("invalid candidate record id")
        title = _published_text(item.get("title"), maximum=1024)
        if title is None:
            raise ContractUnavailable("invalid candidate title")
        author = item.get("author")
        if author is not None:
            author = _published_text(author, maximum=512)
            if author is None:
                raise ContractUnavailable("invalid candidate author")
        if record_id in seen:
            continue
        seen.add(record_id)
        # v0.2 is still unverified. Project *only* bounded, explicit,
        # bibliographic scalars from the candidate fixture/schema.
        # Never trust upstream URLs, entitlement, acquisition or free-form metadata.
        published_isbn = (_published_isbn(item.get("isbn13"))
                          or _published_isbn(item.get("isbn")))
        published_language = _published_text(item.get("language"), maximum=12)
        if published_language and not re.fullmatch(
                r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", published_language):
            published_language = None
        metadata = {"source_id": provider_id.removeprefix("cifericaopds.")}
        if published_isbn:
            metadata["isbn"] = published_isbn
        if published_language:
            metadata["language"] = published_language
        rows.append(SearchResult(
            id=f"{provider_id}:{record_id}", record_id=record_id,
            docbase="CIFERICAOPDS", medium="unknown", title=title,
            author=author,
            type=_published_text(item.get("type"), maximum=80),
            format=_published_text(item.get("format"), maximum=40),
            publisher=_published_text(item.get("publisher"), maximum=256),
            published_at=_published_date(item.get("published_at")),
            description=None, thumbnail_url=None, friendly_url=None,
            scenario="CATALOG", source_provider=provider_id,
            source_credential="__public__", metadata=metadata,
        ))
    return rows


class CifericaSession(ProviderSession):
    def __init__(self, *, client, mode, directory, source_searchable, **kw):
        super().__init__(**kw)
        self._client, self._mode, self._directory = client, mode, directory
        self._source_searchable = source_searchable

    def connect(self):
        self.connected = True  # No discovery or network probe.
        return []

    def close(self):
        self.connected = False

    def search(self, query, scope="all", page=0, field="all"):
        if not isinstance(query, str) or len(query) > 300 or any(
                ord(ch) < 32 or ord(ch) == 127 for ch in query):
            raise ContractUnavailable("invalid search query")
        if type(page) is not int or not 0 <= page <= 100:
            raise ContractUnavailable("invalid search page")
        if page:
            if self._mode == "fixture":
                return []  # One bounded fixture page; never repeat first-page records.
            raise ContractUnavailable("candidate server pagination is unverified")
        # Lock-protected local inventory check; no network.
        if not self._source_searchable():
            raise ContractUnavailable("source inactive or changed; reconnect required")
        sid = self.provider_id.removeprefix("cifericaopds.")
        if self._mode == "fixture":
            path = self._directory / "fixture-results.json"
            data = _read_local_json(path, label="results", optional=True)
            if data is None:
                return []
            if not isinstance(data, dict) or not isinstance(data.get(sid, {"items": []}), dict):
                raise ContractUnavailable("invalid local results fixture")
            values = candidate_results(data.get(sid, {"items": []}), self.provider_id)
            q = str(query).strip().casefold()
            rows = [x for x in values if q in x.title.casefold() or q in (x.author or "").casefold()]
        else:
            # Explicit user search only; candidate HTTP is NOT confirmed v0.2.
            data = self._client.get("/api/v1/search", params={"source_id": sid, "q": str(query)})
            rows = candidate_results(data, self.provider_id)
        if not self._source_searchable():
            raise ContractUnavailable("source changed during search; results discarded")
        return rows


class CifericaProvider(ProviderPlugin):
    def __init__(self, source_id, capabilities, *, client, mode, directory,
                 source_searchable):
        self._client, self._mode, self._directory = client, mode, directory
        self._source_searchable = source_searchable
        self.descriptor = ProviderDescriptor(
            id=f"cifericaopds.{source_id}", display_name=f"CifericaOPDS · {source_id}",
            description="Public metadata only; no acquisition rights",
            state="ready",
            capabilities=ProviderCapabilities(search="catalog.search" in capabilities),
        )

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        if credentials:
            raise ContractUnavailable("virtual sources cannot hold credentials")
        return CifericaSession(
            descriptor=self.descriptor, credential_id=credential_id,
            label=label, credentials={}, options={},
            client=self._client, mode=self._mode, directory=self._directory,
            source_searchable=self._source_searchable,
        )


class CifericaCollection:
    def __init__(self):
        self._lock = RLock()
        self._snapshot = None
        self._callbacks = []
        self._directory = None
        self._client = None
        self._mode = "fixture"
        self._show_synthetic = False
        self._session_epochs = {}
        self._active = False
        self._activation_generation = 0

    def activate(self, context):
        with self._lock:
            self._activation_generation += 1
            # Invalidate sessions from previous activations, including cases
            # where the new cache advertises identical upstream revisions.
            for sid in self._session_epochs:
                self._session_epochs[sid] += 1
            self._snapshot = None
            self._active = False
        cfg = dict(context.config)
        self._mode = cfg.get("mode", "fixture")
        if self._mode not in ("fixture", "candidate_v02"):
            raise ContractUnavailable("unknown plugin mode")
        self._client = JsonClient(cfg.get("base_url", "http://127.0.0.1:8788"),
                                  cfg.get("timeout_seconds", 4))
        self._show_synthetic = bool(cfg.get("show_synthetic", False))
        self._directory = context.data_directory
        path = self._directory / "inventory.json"
        try:
            cached = _read_local_json(path, label="inventory", optional=True)
            if cached is not None:
                self._snapshot = validate_provider_collection_snapshot(
                    cached, parent_plugin_id=PARENT_ID,
                )
        except (ContractUnavailable, ValueError, TypeError, RecursionError):
            # A damaged cache cannot prevent the plugin from activating.
            # It is neither published nor overwritten before an explicit refresh.
            self._snapshot = None
        with self._lock:
            self._active = True
        return self

    def deactivate(self):
        with self._lock:
            self._activation_generation += 1
            self._active = False
            for sid in self._session_epochs:
                self._session_epochs[sid] += 1
            self._callbacks.clear()
            self._directory = None

    def snapshot(self):
        with self._lock:
            if self._snapshot is None:
                raise ContractUnavailable("no validated inventory; explicit refresh required")
            return self._snapshot

    def refresh(self, *, explicit=True):
        if explicit is not True:
            raise ContractUnavailable("discovery must be explicit")
        with self._lock:
            if not self._active or self._directory is None:
                raise ContractUnavailable("plugin is not active")
            activation = self._activation_generation
            directory = self._directory
            mode = self._mode
        if mode == "fixture":
            path = directory / "fixture-sources.json"
            data = _read_local_json(path, label="source")
        else:
            # Experimental candidate route, not an assertion about upstream v0.2.
            data = self._client.get("/api/v1/sources")
        if not isinstance(data, dict):
            raise ContractUnavailable("invalid candidate inventory")
        with self._lock:
            if not self._active or self._activation_generation != activation:
                raise ContractUnavailable("plugin changed during source refresh")
            accepted = candidate_inventory(data, self._snapshot,
                                           show_synthetic=self._show_synthetic)
            if accepted == self._snapshot:
                return accepted
            path = directory / "inventory.json"
            temp = path.with_suffix(".tmp")
            try:
                temp.write_text(json.dumps(asdict(accepted), ensure_ascii=False,
                                           sort_keys=True), encoding="utf-8")
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
            previous = {row.source_id: row for row in self._snapshot.children} if self._snapshot else {}
            current = {row.source_id: row for row in accepted.children}
            for source_id in previous.keys() | current.keys():
                if previous.get(source_id) != current.get(source_id):
                    self._session_epochs[source_id] = self._session_epochs.get(source_id, 0) + 1
            self._snapshot = accepted
            subscribers = tuple(self._callbacks)
        for callback in subscribers:
            try:
                callback()
            except Exception:
                pass
        return accepted

    def _searchable(self, source_id, source_revision, catalog_generation, epoch):
        """Inspect current validated local inventory and source incarnation."""
        with self._lock:
            snapshot = self._snapshot
            if not self._active or snapshot is None or self._session_epochs.get(source_id, 0) != epoch:
                return False
            return any(
                child.source_id == source_id
                and child.status == "ready"
                and "catalog.search" in child.capabilities
                and child.source_revision == source_revision
                and child.catalog_generation == catalog_generation
                for child in snapshot.children
            )

    def create_provider(self, source_id):
        with self._lock:
            snapshot = self._snapshot
            if not self._active or snapshot is None:
                raise ContractUnavailable("no active local source inventory")
            child = next((row for row in snapshot.children
                          if row.source_id == source_id and row.status == "ready"), None)
            epoch = self._session_epochs.get(source_id, 0)
        if child is None:
            raise ContractUnavailable("source absent or disabled")
        return CifericaProvider(
            source_id, child.capabilities, client=self._client, mode=self._mode,
            directory=self._directory,
            source_searchable=lambda: self._searchable(
                source_id, child.source_revision, child.catalog_generation, epoch),
        )

    def subscribe(self, callback):
        with self._lock:
            self._callbacks.append(callback)
        def unsubscribe():
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)
        return unsubscribe


PLUGIN = CifericaCollection()
