from __future__ import annotations

import json
from urllib.parse import quote
from typing import Any

import httpx

from cifereca.plugin_api import ProviderError

from cifereca_kobo_policy import _strict_product_id



class KoboVaultError(ProviderError):
    """Structured Kobo-Vault provider error.

    ``code`` is safe to use for orchestration decisions.  Sensitive response
    bodies are never copied into the exception.
    """

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class KoboVaultClient:
    """Client for the Kobo Vault Provider API v2.3.3 semantics (API v2.3).

    cifereca consumes Vault strictly as a Library provider.  It uses the
    public discovery route, authenticated catalogue/library routes and the
    acquisition-information route.  No legacy Vault agent/job route is used.
    """

    API_PREFIX = "/api/v2"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base_url = str(base_url or "").strip().rstrip("/")
        if not base_url:
            raise ProviderError("Kobo-Vault : URL API manquante")
        if not base_url.startswith(("https://", "http://")):
            raise ProviderError("Kobo-Vault : URL API invalide")
        self.base_url = base_url
        self.token = str(token or "").strip()
        self.last_manifest_rejections: list[dict[str, str]] = []
        self.last_search_merge_rejections: list[dict[str, str]] = []
        self.client = httpx.Client(
            headers={
                "Accept": "application/json",
                "User-Agent": "cifereca-kobo/1.2.1",
            },
            timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else "/" + path
        if path.startswith(self.API_PREFIX + "/") or path == self.API_PREFIX:
            return f"{self.base_url}{path}"
        return f"{self.base_url}{self.API_PREFIX}{path}"

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        """Accept only enumerated provider error codes, never response prose.

        The Vault's message (including a supposed error code) can contain
        URLs, account names, signed query values or bearer tokens. These
        strings must not enter exception messages, logs or sync diagnostics.
        """
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        code = error.get("code")
        if not isinstance(code, str):
            return None
        allowed = {
            "archived_book", "insufficient_scope", "forbidden",
            "invalid_token", "invalid_api_token", "unauthorized",
        }
        return code if code in allowed else None

    def _json(self, method: str, path: str, *, authenticated: bool = True, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        if authenticated:
            if not self.token:
                raise KoboVaultError("Kobo-Vault : token API manquant", code="missing_token", status_code=401)
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.client.request(method, self._url(path), headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise KoboVaultError("Kobo-Vault : erreur de transport", code="network_error") from exc
        if response.is_error:
            status = response.status_code
            # A fixed, minimal vocabulary replaces untrusted provider
            # messages and headers. The delegated 403 is further scoped at
            # the Core boundary because a 403 on /account is not a delegated
            # permission failure.
            provider_code = self._error_code(response)
            if status == 401:
                code, message = "invalid_token", "token API refusé"
            elif status == 403:
                code = "insufficient_scope" if provider_code == "insufficient_scope" else "forbidden"
                message = "accès refusé : autorisations Kobo-Vault insuffisantes"
            elif status == 409 and provider_code == "archived_book":
                code = "archived_book"
                message = "livre archivé : restaurez-le dans votre bibliothèque Kobo avant de réessayer"
            elif status == 409:
                code, message = "conflict", "conflit de ressource (HTTP 409)"
            elif status == 429:
                code, message = "rate_limited", "limite de requêtes Kobo-Vault atteinte (HTTP 429)"
            elif status >= 500:
                code, message = "server_error", f"serveur Kobo-Vault indisponible (HTTP {status})"
            else:
                code, message = "http_error", f"requête Kobo-Vault refusée (HTTP {status})"
            raise KoboVaultError(
                f"Kobo-Vault : {message}",
                code=code,
                status_code=status,
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise KoboVaultError("Kobo-Vault : réponse JSON invalide", code="invalid_response") from exc
        if not isinstance(payload, dict):
            raise KoboVaultError("Kobo-Vault : réponse JSON inattendue", code="invalid_response")
        return payload

    def status(self, *, authenticated: bool = False) -> dict[str, Any]:
        # /status is public in Provider API v2.2+ (courant v2.3.3, api_version=2.3). ``authenticated`` is retained
        # only as a compatibility escape hatch for older v2 deployments.
        return self._json("GET", "/status", authenticated=authenticated)

    def account(self) -> dict[str, Any]:
        payload = self._json("GET", "/account")
        account = payload.get("account")
        return dict(account) if isinstance(account, dict) else payload

    def search_catalog(
        self,
        query: str,
        *,
        field: str = "all",
        limit: int = 50,
        page: int = 0,
        language: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query,
            "field": field,
            "limit": max(1, min(int(limit), 100)),
            "page": max(0, int(page)),
        }
        if language:
            params["language"] = language
        return self._json("GET", "/catalog/search", params=params)

    def search_library(
        self,
        query: str,
        *,
        field: str = "all",
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query,
            "field": field,
            "limit": max(1, min(int(limit), 100)),
        }
        if cursor:
            params["cursor"] = cursor
        return self._json("GET", "/library/search", params=params)

    def search(
        self,
        query: str,
        *,
        field: str = "all",
        scope: str = "all",
        page: int = 0,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Compatibility facade returning a merged ``items`` array.

        ``scope`` is a Paris-side logical selector: ``catalog``, ``library`` or
        ``all``.  API v2.1 deliberately exposes catalog and library as separate
        resources, so the all mode merges by ``product_id`` and lets the library
        projection win for ownership/account facts, while preserving non-empty
        public catalogue bibliographic details absent from the account row.
        No ownership/download state is inferred from catalogue data.
        """
        scope = str(scope or "all").lower()
        self.last_search_merge_rejections = []
        if scope == "catalog":
            return self.search_catalog(query, field=field, limit=limit, page=page)
        if scope == "library":
            return self.search_library(query, field=field, limit=limit, cursor=cursor)

        catalog = self.search_catalog(query, field=field, limit=limit, page=page)
        library = self.search_library(query, field=field, limit=limit, cursor=cursor)
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for source, payload in (("catalog", catalog), ("library", library)):
            items = payload.get("items")
            if not isinstance(items, list):
                self.last_search_merge_rejections.append({
                    "source": source,
                    "type": "invalid_shape",
                    "reason": "items_not_list",
                })
                continue
            for raw in items:
                if not isinstance(raw, dict):
                    self.last_search_merge_rejections.append({
                        "source": source,
                        "type": "invalid_shape",
                        "reason": "item_not_object",
                    })
                    continue
                # Both catalogue and authenticated-library rows need one
                # unambiguous edition key. Numeric/contradictory aliases can
                # otherwise merge unrelated books and their account facts.
                key = _strict_product_id(raw)
                if key is None:
                    self.last_search_merge_rejections.append({
                        "source": source,
                        "type": "invalid_shape",
                        "reason": "missing_product_id",
                    })
                    continue
                if key not in merged:
                    order.append(key)
                if source == "library" and key in merged:
                    # Account records are authoritative for ownership/revision,
                    # but can be a thin manifest without the catalogue synopsis
                    # or bibliographic facets. An empty library field must not
                    # erase evidence already supplied by the public catalogue.
                    catalog_row = merged[key]
                    # Preserve authority *only* from the authenticated
                    # library row. Starting with catalogue data and overlaying
                    # the library used to resurrect public owned/downloadable,
                    # account, revision and acquisition fields whenever the
                    # personal result omitted them. Only explicitly listed
                    # bibliographic facts may be enriched from the catalogue.
                    combined = dict(raw)
                    # The authenticated /library/search endpoint can return a
                    # thin row with neither 'collection' nor 'owned'. Its
                    # membership (not any catalogue field) establishes that
                    # this is a personal-library notice for normalization.
                    combined.setdefault("collection", "library")
                    bibliographic_fields = (
                        "description", "summary", "synopsis", "subjects", "subject",
                        "genres", "categories", "tags", "keywords", "series",
                        "awards", "prizes", "authors", "author", "publisher",
                        "publication_date", "published_at", "language", "languages",
                        "cover_url", "thumbnail_url", "image", "images",
                        "product_url",
                    )
                    for name in bibliographic_fields:
                        if (name in catalog_row and catalog_row[name] not in (None, "", (), [], {})
                                and raw.get(name) in (None, "", (), [], {})):
                            combined[name] = catalog_row[name]
                    merged[key] = combined
                else:
                    merged[key] = dict(raw)
        return {
            "items": [merged[key] for key in order],
            "catalog": catalog,
            "library": library,
        }

    def library_manifest_page(self, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        if cursor:
            params["cursor"] = cursor
        return self._json("GET", "/library/manifest", params=params)

    def library_manifest(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        self.last_manifest_rejections = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(100):
            payload = self.library_manifest_page(cursor=cursor)
            items = payload.get("items")
            if isinstance(items, list):
                for raw in items:
                    if isinstance(raw, dict):
                        # This is the provider's last trust boundary before
                        # Core receives product identifiers for a future
                        # account-specific acquisition. Reject ambiguous IDs
                        # here as well as in advisory policy counters: Core's
                        # legacy reader does not yet consume that policy.
                        if _strict_product_id(raw) is None:
                            self.last_manifest_rejections.append({
                                "type": "invalid_shape",
                                "reason": "unverifiable_product_id",
                            })
                            continue
                        rows.append(dict(raw))
                    else:
                        self.last_manifest_rejections.append({
                            "type": "invalid_shape",
                            "reason": "item_not_object",
                        })
            else:
                # Missing/null items on an otherwise successful page is not
                # an authoritative empty collection: expose the corruption.
                self.last_manifest_rejections.append({
                    "type": "invalid_shape",
                    "reason": "items_not_list",
                })
            next_cursor = payload.get("next_cursor")
            # A malformed cursor is NOT the end of an authoritative personal
            # manifest: e.g. 0, False, {}, whitespace or a control character
            # would previously terminate early or be converted to a query.
            # Vault cursors are opaque printable tokens, never user-facing.
            if next_cursor is None or next_cursor == "":
                break
            if (
                type(next_cursor) is not str
                or not 1 <= len(next_cursor) <= 2048
                or any(not 33 <= ord(ch) <= 126 for ch in next_cursor)
            ):
                raise KoboVaultError(
                    "Kobo-Vault : curseur de manifeste invalide",
                    code="invalid_pagination",
                )
            cursor = next_cursor
            if cursor in seen:
                raise KoboVaultError(
                    "Kobo-Vault : pagination manifest en boucle",
                    code="invalid_pagination",
                )
            seen.add(cursor)
        else:
            raise KoboVaultError("Kobo-Vault : manifest trop volumineux ou pagination invalide", code="invalid_pagination")
        return rows

    def library_item(self, product_id: str) -> dict[str, Any]:
        payload = self._json("GET", f"/library/{quote(str(product_id), safe='')}")
        item = payload.get("item")
        return dict(item) if isinstance(item, dict) else payload

    def acquisition(
        self,
        product_id: str,
        *,
        revision_id: str | None = None,
        mode: str = "descriptor",
        display_profile: str | None = None,
        include_acsm: bool | None = None,
    ) -> dict[str, Any]:
        mode = str(mode or "descriptor").lower()
        if mode not in {"descriptor", "delegated"}:
            raise ProviderError(f"Kobo-Vault : mode d'acquisition invalide ({mode})")
        body: dict[str, Any] = {"mode": mode}
        if revision_id:
            body["revision_id"] = revision_id
        if display_profile is not None:
            profile = str(display_profile or "").strip()
            if profile not in {"Android", "Desktop"}:
                raise ProviderError(f"Kobo-Vault : DisplayProfile invalide ({profile or 'vide'})")
            body["display_profile"] = profile
        if include_acsm is not None:
            body["include_acsm"] = bool(include_acsm)
        return self._json("POST", f"/library/{quote(str(product_id), safe='')}/acquisition", json=body)
