from __future__ import annotations

import json
from urllib.parse import quote
from typing import Any, Callable

import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

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
        self.client = HTTPX_TRANSPORT.client(producer="provider.kobo",
            headers={
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0)),
            # Provider API requests must never follow redirect targets,
            # especially POST /library/{id}/acquisition: the revision and
            # private account request body could otherwise be sent to a
            # third-party origin following a 307/308 response.
            follow_redirects=False,
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
    def _checked_search_text(
        value: Any, *, maximum: int = 4096, optional: bool = False,
    ) -> str | None:
        # A query/cursor may be opaque or contain Unicode but must not be an
        # arbitrary object or an unbounded/control-character-bearing value.
        # Never interpolate caller input into public provider errors.
        if optional and value is None:
            return None
        if type(value) is not str or len(value) > maximum or (
            not optional and maximum <= 256 and not value
        ) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ProviderError("Kobo-Vault : paramètre de recherche invalide")
        return value

    @staticmethod
    def _checked_pagination(value: Any, *, minimum: int, maximum: int) -> int:
        # Legacy numeric-string inputs remain supported, but booleans, floats
        # and arbitrary objects must not be coerced by int(), whose error may
        # echo a private token or signed URL.
        if type(value) is int:
            number = value
        elif (
            type(value) is str and 1 <= len(value) <= 16
            and (
                value.isascii() and value.isdecimal()
                or (
                    value.startswith("-") and len(value) > 1
                    and value[1:].isascii() and value[1:].isdecimal()
                )
            )
        ):
            number = int(value)
        else:
            raise ProviderError("Kobo-Vault : pagination invalide")
        return max(minimum, min(number, maximum))

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
        # Do not let callers of the private helper re-enable redirects. An
        # unexpected Location header, even to this same host, cannot attest
        # that the destination has the same authentication and rights policy.
        kwargs.pop("follow_redirects", None)
        try:
            response = self.client.request(
                method, self._url(path), headers=headers,
                follow_redirects=False, **kwargs,
            )
        except httpx.HTTPError as exc:
            raise KoboVaultError("Kobo-Vault : erreur de transport", code="network_error") from exc
        if 300 <= response.status_code < 400:
            # Never reflect Location, including its query/user-info. A 3xx is
            # not an authoritative JSON provider response or an authorization
            # to retry the private request elsewhere.
            raise KoboVaultError(
                "Kobo-Vault : redirection HTTP refusée",
                code="redirect_denied",
                status_code=response.status_code,
            )
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
            "q": self._checked_search_text(query),
            "field": self._checked_search_text(field, maximum=128),
            "limit": self._checked_pagination(limit, minimum=1, maximum=100),
            "page": self._checked_pagination(page, minimum=0, maximum=1_000_000),
        }
        lang = self._checked_search_text(language, maximum=128, optional=True)
        if lang:
            params["language"] = lang
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
            "q": self._checked_search_text(query),
            "field": self._checked_search_text(field, maximum=128),
            "limit": self._checked_pagination(limit, minimum=1, maximum=100),
        }
        checked_cursor = self._checked_search_text(cursor, optional=True)
        if checked_cursor:
            params["cursor"] = checked_cursor
        return self._json("GET", "/library/search", params=params)

    @staticmethod
    def _strip_public_private_fields(value: Any, depth: int = 0) -> Any:
        """Keep public bibliography, never nested account or acquisition data.

        Vault JSON can wrap rights in metadata/variants/lists, with snake_case
        or camelCase keys. Removing only top-level keys leaks those fields into
        SearchResult.metadata and into the public catalogue response envelope.
        The depth cap also fails closed on an abnormally nested JSON structure.
        """
        private_keys = {
            "owned", "isowned", "downloadable", "candownload", "archived",
            "account", "accounts", "accountid", "customerid", "userid",
            "credential", "credentials", "credentialid", "sourcecredential",
            "revisionid", "acquisition", "entitlement", "entitlements",
            "rights", "ownership", "localpath", "downloadurl", "licenseurl",
            "signedurl", "authorization", "accesstoken", "refreshtoken",
            "apitoken", "authtoken", "bearertoken", "sessiontoken",
            "password", "passwd", "secret", "clientsecret", "clientid",
            "apikey", "secretkey", "privatekey", "accesskey", "session",
            "sessionid", "sessionkey", "cookie", "cookies", "csrftoken",
            "csrf", "auth", "authentication", "bearer", "oauth",
            "drmlicense", "licensekey", "licencekey", "acsmurl",
            "library",
        }
        if depth >= 16:
            return None
        if type(value) is dict:
            public: dict[str, Any] = {}
            for key, child in value.items():
                if type(key) is not str:
                    continue
                normalized = "".join(char for char in key.casefold() if char.isalnum())
                # Public descriptions can contain novel nested JSON field
                # names. Unknown credential suffixes/transport-key variants
                # must not survive simply because they weren't in the fixed
                # original denylist (e.g. partner_refresh_token, apiKey).
                if (
                    normalized in private_keys
                    or normalized.endswith((
                        "token", "secret", "password", "apikey",
                        "privatekey", "accesskey", "sessionid",
                        "sessionkey", "cookie", "credential",
                    ))
                    or normalized.startswith((
                        "signeddownload", "signedlicense", "signedlicence",
                        "acquisitionurl", "acquisitionlink",
                        "downloadurl", "downloadlink", "downloadtoken",
                        "localpath", "localfile",
                    ))
                ):
                    continue
                public[key] = KoboVaultClient._strip_public_private_fields(
                    child, depth + 1
                )
            return public
        if type(value) is list:
            return [
                KoboVaultClient._strip_public_private_fields(child, depth + 1)
                for child in value
            ]
        return value

    @classmethod
    def _public_catalog_hit(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Public discovery never attests ownership or a download entitlement."""
        item = cls._strip_public_private_fields(raw)
        # An upstream catalogue can falsely describe a public hit as a
        # library collection or as owned. Explicitly override that claim.
        item["owned"] = False
        item["collection"] = "catalog"
        return item

    @classmethod
    def _public_catalog_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        # Sanitize envelope metadata as well as individual hit metadata: both
        # are observable in scope=all through result["catalog"].
        public = cls._strip_public_private_fields(payload)
        items = public.get("items")
        if type(items) is not list:
            return public
        public["items"] = [
            cls._public_catalog_hit(row) if type(row) is dict else row
            for row in items
        ]
        return public

    def _quarantine_personal_search(
        self, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Reject ambiguous personal editions before returning *any* search scope.

        Identical IDs may represent conflicting revisions or distinct accounts.
        Invalid alias pairs also taint each individually valid edition key.
        Only constant rejection reasons cross the provider boundary.
        """
        rows = payload.get("items")
        if not isinstance(rows, list):
            return payload
        counts: dict[str, int] = {}
        tainted: set[str] = set()
        for raw in rows:
            if type(raw) is not dict:
                continue
            product = _strict_product_id(raw)
            if product is not None:
                counts[product] = counts.get(product, 0) + 1
            else:
                for alias in ("product_id", "id"):
                    candidate = _strict_product_id({alias: raw.get(alias)})
                    if candidate is not None:
                        tainted.add(candidate)
        tainted.update(key for key, count in counts.items() if count > 1)

        accepted: list[dict[str, Any]] = []
        for raw in rows:
            if type(raw) is not dict:
                reason, category = "item_not_object", "invalid_shape"
            else:
                product = _strict_product_id(raw)
                if product is None:
                    reason, category = "missing_product_id", "invalid_shape"
                elif product in tainted:
                    reason, category = "duplicate_product_id", "invalid_scope"
                else:
                    accepted.append(dict(raw))
                    continue
            self.last_search_merge_rejections.append({
                "source": "library", "type": category, "reason": reason,
            })
        return {**payload, "items": accepted}

    def search(
        self,
        query: str,
        *,
        field: str = "all",
        scope: str = "all",
        page: int = 0,
        limit: int = 50,
        cursor: str | None = None,
        private_guard: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Compatibility facade returning a merged ``items`` array.

        ``scope`` is a Paris-side logical selector: ``catalog``, ``library`` or
        ``all``.  API v2.1 deliberately exposes catalog and library as separate
        resources, so the all mode merges by ``product_id`` and lets the library
        projection win for ownership/account facts, while preserving non-empty
        public catalogue bibliographic details absent from the account row.
        No ownership/download state is inferred from catalogue data.
        """
        # Unknown scopes must not silently broaden a catalog-only lookup
        # into an authenticated two-endpoint library query.
        self.last_search_merge_rejections = []
        if type(scope) is not str or scope.lower() not in {"catalog", "library", "all"}:
            raise ProviderError("Kobo-Vault : portée de recherche invalide")
        scope = scope.lower()
        if scope in {"library", "all"}:
            # all starts with a public GET. Validate personal cursor before
            # that first call, so a bad private cursor never causes partial
            # network activity or misleading public-only success.
            self._checked_search_text(cursor, optional=True)
        if scope == "catalog":
            return self._public_catalog_payload(
                self.search_catalog(query, field=field, limit=limit, page=page)
            )
        if scope == "library":
            if private_guard is not None:
                private_guard()
            library = self.search_library(query, field=field, limit=limit, cursor=cursor)
            if private_guard is not None:
                private_guard()
            return self._quarantine_personal_search(library)

        catalog = self._public_catalog_payload(
            self.search_catalog(query, field=field, limit=limit, page=page)
        )
        # Public discovery might have taken seconds; Core can revoke or
        # rebind the owner before the following *private* library GET.
        if private_guard is not None:
            private_guard()
        library = self.search_library(query, field=field, limit=limit, cursor=cursor)
        if private_guard is not None:
            private_guard()
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for source, payload in (("catalog", catalog), ("library", library)):
            if source == "library":
                payload = self._quarantine_personal_search(payload)
                library = payload
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
                    # A personal hit with no matching public catalogue card
                    # still came from authenticated /library/search. Its
                    # absent collection flag must not be treated as catalogue
                    # absence. An explicit owned=False is never overridden.
                    individual = dict(raw)
                    if source == "library" and individual.get("owned") is None:
                        individual.setdefault("collection", "library")
                    merged[key] = individual
        return {
            "items": [merged[key] for key in order],
            "catalog": catalog,
            "library": library,
        }

    def library_manifest_page(self, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": self._checked_pagination(limit, minimum=1, maximum=500),
        }
        checked_cursor = self._checked_search_text(cursor, optional=True)
        if checked_cursor:
            params["cursor"] = checked_cursor
        return self._json("GET", "/library/manifest", params=params)

    def library_manifest(self, *, page_guard: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        self.last_manifest_rejections = []
        # Rejected conflicting aliases can precede/follow an apparently valid
        # row on another page. Remember both *individually valid* candidates
        # until pagination completes, then quarantine all their occurrences.
        tainted_products: set[str] = set()
        # Multiple individually valid rows for an edition are still ambiguous:
        # revisions (or the same page replayed across cursors) must never be
        # resolved by first/last-wins, especially for Core's legacy backup.
        product_counts: dict[str, int] = {}
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(100):
            # A Core rebind between cursor pages revokes authority for the
            # *next* private request, not merely the returned final snapshot.
            if page_guard is not None:
                page_guard()
            payload = self.library_manifest_page(cursor=cursor)
            if page_guard is not None:
                page_guard()
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
                            for alias in ("product_id", "id"):
                                candidate = _strict_product_id({alias: raw.get(alias)})
                                if candidate is not None:
                                    tainted_products.add(candidate)
                            self.last_manifest_rejections.append({
                                "type": "invalid_shape",
                                "reason": "unverifiable_product_id",
                            })
                            continue
                        product = _strict_product_id(raw)
                        product_counts[product] = product_counts.get(product, 0) + 1
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
        duplicate_products = {
            product for product, count in product_counts.items() if count > 1
        }
        if not tainted_products and not duplicate_products:
            return rows
        safe_rows: list[dict[str, Any]] = []
        for row in rows:
            product = _strict_product_id(row)
            if product in tainted_products:
                # Only constant reasons reach public diagnostics; no product,
                # account, revision or signed acquisition URL may escape.
                self.last_manifest_rejections.append({
                    "type": "invalid_shape",
                    "reason": "tainted_product_id",
                })
            elif product in duplicate_products:
                self.last_manifest_rejections.append({
                    "type": "invalid_scope",
                    "reason": "duplicate_product_id",
                })
            else:
                safe_rows.append(row)
        return safe_rows

    @staticmethod
    def _checked_product_id(product_id: object) -> str:
        # A direct detail/acquisition caller can bypass inventory normalization.
        # Apply exactly the same edition-key checks as public and personal
        # manifests, before a private HTTP request is ever constructed.
        candidate = _strict_product_id({"product_id": product_id})
        if candidate is None:
            raise ProviderError("Kobo-Vault : identifiant de produit non vérifiable")
        return candidate

    def library_item(self, product_id: str) -> dict[str, Any]:
        product = self._checked_product_id(product_id)
        payload = self._json("GET", f"/library/{quote(product, safe='')}")
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
        product = self._checked_product_id(product_id)
        if type(mode) is not str or mode.lower() not in {"descriptor", "delegated"}:
            # Never echo caller-supplied control fields in an exception: they
            # can contain bearer tokens, signed URLs and account identifiers.
            raise ProviderError("Kobo-Vault : mode d'acquisition invalide")
        mode = mode.lower()
        body: dict[str, Any] = {"mode": mode}
        if revision_id is not None and revision_id != "":
            # A revision selector is a private acquisition authority. Never
            # stringify booleans/numbers or forward untrusted control chars.
            if _strict_product_id({"product_id": revision_id}) is None:
                raise ProviderError("Kobo-Vault : identifiant de révision invalide")
            body["revision_id"] = revision_id
        if display_profile is not None:
            if type(display_profile) is not str or display_profile.strip() not in {"Android", "Desktop"}:
                raise ProviderError("Kobo-Vault : DisplayProfile invalide")
            body["display_profile"] = display_profile.strip()
        if include_acsm is not None:
            # bool("false") is True. A malformed flag must not unexpectedly
            # enable an ACSM export; JSON boolean is the only accepted type.
            if type(include_acsm) is not bool:
                raise ProviderError("Kobo-Vault : option ACSM invalide")
            body["include_acsm"] = include_acsm
        return self._json("POST", f"/library/{quote(product, safe='')}/acquisition", json=body)
