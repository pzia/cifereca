from __future__ import annotations

import json
from urllib.parse import quote
from typing import Any

import httpx

from cifereca.plugin_api import ProviderError, redact_text



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
    def _error_parts(response: httpx.Response) -> tuple[str | None, str]:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "").strip() or None
                text = str(error.get("message") or error.get("code") or "").strip()
                if text:
                    return code, text
            text = str(payload.get("message") or "").strip()
            if text:
                return None, text
        return None, f"HTTP {response.status_code}"

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
            code, text = self._error_parts(response)
            if response.status_code == 401:
                text = text if text and text != "HTTP 401" else "token API refusé"
            elif response.status_code == 403:
                text = text if text and text != "HTTP 403" else "scope insuffisant"
            elif response.status_code == 409 and code == "archived_book":
                text = (
                    text if text and text != "HTTP 409"
                    else "livre archivé : restaurez-le dans votre bibliothèque Kobo avant de réessayer"
                )
            elif response.status_code == 429:
                retry = response.headers.get("retry-after")
                if retry:
                    text = f"{text} (réessayer dans {retry}s)"
            raise KoboVaultError(
                f"Kobo-Vault : {redact_text(text)}",
                code=code,
                status_code=response.status_code,
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
        projection win when both endpoints return the same title.
        """
        scope = str(scope or "all").lower()
        if scope == "catalog":
            return self.search_catalog(query, field=field, limit=limit, page=page)
        if scope == "library":
            return self.search_library(query, field=field, limit=limit, cursor=cursor)

        catalog = self.search_catalog(query, field=field, limit=limit, page=page)
        library = self.search_library(query, field=field, limit=limit, cursor=cursor)
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for payload in (catalog, library):
            items = payload.get("items")
            if not isinstance(items, list):
                continue
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("product_id") or raw.get("id") or "").strip()
                if not key:
                    continue
                if key not in merged:
                    order.append(key)
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
                        rows.append(dict(raw))
                    else:
                        self.last_manifest_rejections.append({
                            "type": "invalid_shape",
                            "reason": "item_not_object",
                        })
            elif items is not None:
                self.last_manifest_rejections.append({
                    "type": "invalid_shape",
                    "reason": "items_not_list",
                })
            next_cursor = payload.get("next_cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
            if cursor in seen:
                raise KoboVaultError("Kobo-Vault : pagination manifest en boucle", code="invalid_pagination")
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
