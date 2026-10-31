"""CifericaOPDS: static, offline-first experimental provider.collection pilot.

The real upstream OpenAPI v0.2 is not available: candidate_v02 routes and JSON
are explicitly NON-NORMATIVE. No network access at activate()/snapshot().
Only cifereca.plugin_api is imported; no private registry access or remote code.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
import re
import json
import math
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from urllib.parse import urlencode, urlsplit, urlunsplit
import httpx

from cifereca.plugin_api import (
    HTTPX_TRANSPORT,
    ProviderCapabilities, ProviderSearchCapabilities, ProviderDescriptor, ProviderPlugin, ProviderSession,
    SearchResult, ProviderCollectionSnapshot, validate_provider_collection_snapshot,
)

PARENT_ID = "org.cifereca.integration.cifericaopds"
MAX_JSON_BYTES = 1024 * 1024
MAX_RESULTS = 500


class ContractUnavailable(RuntimeError):
    """Unavailable, invalid or unverified server contract."""


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


def _decode_strict_json(raw: bytes):
    """Reject ambiguous or non-finite JSON before it enters local/HTTP state."""
    def reject_constant(_value):
        raise ValueError("invalid JSON numeric constant")

    def reject_duplicate_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def parse_finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result

    return json.loads(
        raw.decode("utf-8"), parse_constant=reject_constant,
        parse_float=parse_finite_float, object_pairs_hook=reject_duplicate_fields,
    )


class JsonClient:
    def __init__(self, base_url, timeout=4):
        self.base_url = safe_base_url(base_url)
        try:
            if isinstance(timeout, bool):
                raise ValueError("invalid timeout")
            seconds = float(timeout)
            if not math.isfinite(seconds):
                raise ValueError("invalid timeout")
            self.timeout = min(20.0, max(0.5, seconds))
        except (TypeError, ValueError, OverflowError):
            raise ContractUnavailable("invalid configured timeout") from None
        # No proxies from HTTP_PROXY/HTTPS_PROXY or platform settings:
        # even requests to a loopback fixture must remain on loopback.

    def get(self, endpoint: str, *, params=None):
        # Do not let child providers turn this candidate read-only transport
        # into a general-purpose HTTP client.
        if endpoint == "/api/v1/sources":
            if params is not None:
                raise ContractUnavailable("unexpected discovery parameters")
            query_string = ""
        elif endpoint == "/api/v1/search":
            if not isinstance(params, dict) or set(params) != {"source_id", "q"}:
                raise ContractUnavailable("invalid search parameters")
            source_id, query = params["source_id"], params["q"]
            if not isinstance(source_id, str) or not re.fullmatch(
                    r"[a-z0-9][a-z0-9_-]{0,63}", source_id):
                raise ContractUnavailable("invalid candidate source id")
            if not isinstance(query, str) or len(query) > 300 or any(
                    ord(ch) < 32 or ord(ch) == 127 for ch in query):
                raise ContractUnavailable("invalid candidate query")
            query_string = "?" + urlencode({"source_id": source_id, "q": query})
        else:
            raise ContractUnavailable("endpoint not allowed")
        url = self.base_url + endpoint + query_string
        headers = {
            "Accept": "application/json", "Accept-Encoding": "identity",
        }
        try:
            with HTTPX_TRANSPORT.client(producer="cifericaopds", timeout=self.timeout,
                                        trust_env=False, follow_redirects=False) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if 300 <= response.status_code < 400:
                        raise ContractUnavailable("redirect refused")
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ContractUnavailable("unexpected HTTP status")
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
                    if content_type != "application/json":
                        raise ContractUnavailable("invalid JSON content type")
                    raw = bytearray()
                    for chunk in response.iter_raw(chunk_size=64 * 1024):
                        raw.extend(chunk)
                        if len(raw) > MAX_JSON_BYTES:
                            raise ContractUnavailable("oversized JSON response")
        except (httpx.HTTPError, OSError, TimeoutError):
            raise ContractUnavailable("server unavailable") from None
        if len(raw) > MAX_JSON_BYTES:
            raise ContractUnavailable("oversized JSON response")
        try:
            result = _decode_strict_json(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ContractUnavailable("invalid server JSON") from None
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
        # Also refuse symlinks on systems without O_NOFOLLOW.
        if path.is_symlink():
            raise ContractUnavailable(f"unreadable local {label} JSON")
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
        # Use exactly the same strict JSON grammar for local and HTTP data.
        return _decode_strict_json(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ContractUnavailable(f"invalid local {label} JSON") from None


def _persist_inventory(path: Path, snapshot) -> None:
    """Publish a private inventory atomically; never follow a known tmp path."""
    raw = json.dumps(asdict(snapshot), ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise ContractUnavailable("oversized local inventory JSON")
    temp_name = None
    try:
        # Randomized, exclusive temporary file in the same directory ensures
        # os.replace() is atomic and avoids following a pre-existing .tmp link.
        with NamedTemporaryFile(
            mode="wb", prefix=".cifericaopds-inventory-", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError:
        raise ContractUnavailable("local inventory persistence unavailable") from None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


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



# Untrusted catalogue values can flow into the global search index,
# offline cache and GTK. Suppress invisible Unicode direction overrides and
# unredacted key/value credentials *before* any normalization.
_PRIVATE_PUBLISHED_VALUE = re.compile(
    r"(?:https?://|file://|ftp://|www\.|@|/home/|/Users/|"
    r"[A-Za-z]:\\Users\\|"
    r"\b(?:access[_-]?token|refresh[_-]?token|api[_-]?key|password|"
    r"passphrase|authorization|cookie|secret|session[_-]?id|token|bearer)"
    r"\s*[:=]|\bbearer\s+[^\s,;]+)",
    re.I,
)


def _published_text(value, *, maximum=256):
    """Allow bounded public bibliography, not control codes or credentials."""
    if type(value) is not str or len(value) > maximum * 2:
        return None
    # Check *before* whitespace normalization, which could hide a CR/tab.
    # Bidi overrides/isolates, invisible direction marks, invalid surrogates
    # and C1 controls may visually spoof bibliographic fields and exports.
    if any(
        ord(ch) < 32 or 0x7F <= ord(ch) <= 0x9F
        or ord(ch) == 0x061C or ord(ch) in (0x200E, 0x200F)
        or 0x202A <= ord(ch) <= 0x202E
        or 0x2066 <= ord(ch) <= 0x2069
        or 0xD800 <= ord(ch) <= 0xDFFF
        for ch in value
    ):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum:
        return None
    if _PRIVATE_PUBLISHED_VALUE.search(cleaned):
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
    if len(raw) != 13 or not raw.isascii() or not raw.isdecimal() or not raw.startswith(("978", "979")):
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
        # The candidate v0.2 GET /search has only q/source_id: it cannot
        # guarantee provider-side filtering by ISBN, author or language.
        # Never present a broad query as an exact attributed search.
        if type(field) is not str or field not in {
            "all", "title", "author", "isbn", "language",
        }:
            raise ContractUnavailable("unsupported candidate search field")
        if self._mode != "fixture" and field != "all":
            raise ContractUnavailable("candidate server search field unverified")
        if type(scope) is not str or scope != "all":
            # Neither local fixture nor candidate HTTP API attests a
            # physical/digital/private scope filter.
            raise ContractUnavailable("unsupported candidate search scope")
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
            if field == "isbn":
                # Exact valid ISBN-13 only; never match a title containing the
                # same digits, or an unverified provider-specific identifier.
                canonical = _published_isbn(query.strip())
                rows = [
                    x for x in values
                    if canonical and x.metadata.get("isbn") == canonical
                ]
            elif field == "language":
                rows = [
                    x for x in values
                    if str(x.metadata.get("language") or "").casefold() == q
                ]
            elif field == "title":
                rows = [x for x in values if q in x.title.casefold()]
            elif field == "author":
                rows = [x for x in values if q in (x.author or "").casefold()]
            else:
                rows = [
                    x for x in values
                    if q in x.title.casefold() or q in (x.author or "").casefold()
                ]
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
            search_capabilities=ProviderSearchCapabilities(
                fields=("all", "title", "author", "isbn", "language")
                if mode == "fixture" else ("all",),
                scopes=("all",),
                pagination="none",
                notes=(
                    "Filtres de champ exécutés localement sur la fixture bornée ; aucune pagination réseau.",
                ) if mode == "fixture" else (
                    "Mode HTTP candidat v0.2 : seul q/source_id est attesté ; champs spécialisés et pagination non vérifiés.",
                ),
            ),
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
        # Raw accepted inventory, including hidden synthetic sources. The
        # public snapshot is a local visibility projection of this inventory.
        self._full_snapshot = None
        self._callbacks = []
        self._directory = None
        self._client = None
        self._mode = "fixture"
        self._show_synthetic = False
        self._session_epochs = {}
        self._active = False
        self._activation_generation = 0

    def activate(self, context):
        # Retire the previous incarnation before any potentially blocking
        # configuration/cache I/O. Only the latest activation may publish.
        with self._lock:
            self._activation_generation += 1
            activation = self._activation_generation
            for sid in self._session_epochs:
                self._session_epochs[sid] += 1
            self._active = False
            self._snapshot = None
            self._full_snapshot = None
            self._client = None
            self._directory = None
            # Subscriptions from a retired Core instance must not observe
            # discoveries made under a replacement configuration/identity.
            self._callbacks.clear()

        cfg = dict(context.config)
        mode = cfg.get("mode", "fixture")
        if mode not in ("fixture", "candidate_v02"):
            raise ContractUnavailable("unknown plugin mode")
        client = JsonClient(cfg.get("base_url", "http://127.0.0.1:8788"),
                            cfg.get("timeout_seconds", 4))
        show_synthetic = bool(cfg.get("show_synthetic", False))
        directory = context.data_directory
        full_snapshot = None
        visible_snapshot = None
        try:
            cached = _read_local_json(
                directory / "inventory.json", label="inventory", optional=True,
            )
            if cached is not None:
                full_snapshot = validate_provider_collection_snapshot(
                    cached, parent_plugin_id=PARENT_ID,
                )
                visible_snapshot = self._visible_snapshot(
                    full_snapshot, show_synthetic=show_synthetic,
                )
        except (ContractUnavailable, ValueError, TypeError, RecursionError):
            # An invalid cache must not break activation or leak private paths.
            # An explicit refresh is required before any source is published.
            full_snapshot = None
            visible_snapshot = None

        with self._lock:
            if self._activation_generation != activation:
                # A later activate()/deactivate() retired this invocation.
                # Never revive it or overwrite its chosen cache and endpoint.
                raise ContractUnavailable("plugin changed during activation")
            self._mode = mode
            self._client = client
            self._show_synthetic = show_synthetic
            self._directory = directory
            self._full_snapshot = full_snapshot
            self._snapshot = visible_snapshot
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
            self._client = None
            self._full_snapshot = None
            self._snapshot = None

    def snapshot(self):
        with self._lock:
            if not self._active or self._snapshot is None:
                raise ContractUnavailable("no validated inventory; plugin inactive or explicit refresh required")
            return self._snapshot

    def _visible_snapshot(self, full, *, show_synthetic=None):
        if show_synthetic is None:
            show_synthetic = self._show_synthetic
        if full is None or show_synthetic:
            return full
        return replace(full, children=tuple(
            row for row in full.children if row.kind != "synthetic.merged"
        ))

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
            # Validate and cache the *full* upstream inventory. Display
            # options must never rewrite generation semantics or persist a
            # lossy, identity-dependent view of source capabilities.
            accepted_full = candidate_inventory(
                data, self._full_snapshot, show_synthetic=True,
            )
            accepted = self._visible_snapshot(accepted_full)
            if accepted_full == self._full_snapshot and accepted == self._snapshot:
                return accepted
            if accepted_full != self._full_snapshot:
                _persist_inventory(directory / "inventory.json", accepted_full)
            previous = {row.source_id: row for row in self._snapshot.children} if self._snapshot else {}
            current = {row.source_id: row for row in accepted.children}
            for source_id in previous.keys() | current.keys():
                if previous.get(source_id) != current.get(source_id):
                    self._session_epochs[source_id] = self._session_epochs.get(source_id, 0) + 1
            self._full_snapshot = accepted_full
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
