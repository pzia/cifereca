from __future__ import annotations
from typing import Any
from urllib.parse import urljoin, urlsplit

from cifereca.plugin_api import ProviderError, SearchResult, normalize_language_tag, description_text, description_element
from cifereca_kobo_policy import _strict_product_id
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


class KoboCatalogService:
    """Credential-bound Kobo catalogue projection.

    The service owns catalogue normalization/search semantics.  The personal
    library provider consumes this service rather than keeping catalogue policy
    mixed with ownership/manifest logic.
    """
    def __init__(self, client: Any, *, credential_id: str, provider_id: str = "kobo") -> None:
        self.client = client
        self.credential_id = credential_id
        self.provider_id = provider_id
        self.last_search_rejections: list[dict[str, str]] = []

    def cover_url(self, item: dict[str, Any]) -> str | None:
        """Only URL fields in cover structures; never turn an image ID into a URL."""
        keys = {"url", "src", "href", "large", "medium", "thumbnail", "small", "cover_url", "thumbnail_url", "image_url", "coverimageurl", "imageurl"}
        budget = [256]
        def valid(value):
            if not isinstance(value, str) or len(value) > 8192:return None
            text=value.strip()
            if not text or any(ord(c)<32 for c in text):return None
            try:
                raw=urlsplit(text)
                if not raw.scheme and not raw.netloc and '/' not in raw.path and '.' not in raw.path:return None
                if raw.scheme and raw.scheme.lower() not in {'http','https'}:return None
                resolved=urljoin(self.client.base_url.rstrip('/')+'/',text)
                parsed=urlsplit(resolved)
                if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:return None
                parsed.port
                return resolved
            except ValueError:return None
        def candidate(value, depth=0):
            budget[0]-=1
            if depth>6 or budget[0]<0:return None
            if isinstance(value,str):return valid(value)
            if isinstance(value,dict):
                for key,nested in value.items():
                    if str(key).casefold() in keys:
                        found=candidate(nested,depth+1)
                        if found:return found
                # Named image variants can be nested, but opaque strings/IDs
                # outside recognised fields must not become a relative URL.
                for nested in value.values():
                    if isinstance(nested,(dict,list)):
                        found=candidate(nested,depth+1)
                        if found:return found
            elif isinstance(value,list):
                for nested in value:
                    found=candidate(nested,depth+1)
                    if found:return found
            return None
        for key in ("cover_url", "thumbnail_url", "image_url", "cover", "image", "images", "covers", "CoverImageUrl", "ImageUrl"):
            found=candidate(item.get(key))
            if found:return found
        return None

    @staticmethod
    def _price(item: dict[str, Any]) -> tuple[str | None, str | None]:
        price = item.get("price")
        if isinstance(price, dict):
            amount = price.get("amount")
            currency = price.get("currency")
            return (str(amount) if amount is not None else None, str(currency) if currency else None)
        return (str(price) if price is not None else None, str(item.get("currency")) if item.get("currency") else None)

    def normalize_item(self, item: dict[str, Any], *, default_collection: str = "catalog") -> SearchResult:
        # Keep the same edition identity checks as the Vault search/manifest
        # boundary. A direct catalogue normalizer must not accept conflicting
        # aliases, coerce numeric IDs, or trim malformed keys into another
        # product that can inherit personal ownership.
        product_id = _strict_product_id(item)
        if product_id is None:
            raise ProviderError("Kobo-Vault : identifiant de notice non vérifiable")
        # Public bibliographic arrays are not rights evidence, but malformed
        # upstream objects must not be stringified into UI text. Some plugin
        # callers provide dict-like fixtures with custom __str__ methods;
        # only explicit text authors may contribute to a public byline.
        authors = item.get("authors")
        if type(authors) in (list, tuple):
            accepted_authors = [
                value.strip() for value in authors
                if type(value) is str and value.strip()
            ]
            author = ", ".join(accepted_authors) or None
        else:
            accepted_authors = []
            raw_author = item.get("author")
            author = raw_author.strip() or None if type(raw_author) is str else None
        fmt = str(item.get("format") or item.get("file_format") or "").strip().upper() or None
        content_type = str(item.get("content_type") or "ebook").lower()
        owned = item.get("owned")
        downloadable = item.get("downloadable")
        # Vault flags must be JSON booleans. Truthiness silently turns
        # "false"/"0" into True, fabricating a right or a downloadable
        # artifact and potentially triggering an unauthorized acquisition.
        # An authenticated personal manifest attests owned books even when
        # an archived item's own collection is "archived" rather than "library".
        # Public catalog normalization still must not infer any ownership.
        # Explicit false/malformed owned flags always override this inference.
        collection = str(
            item.get("collection") or ("library" if owned is True else default_collection)
        ).lower()
        owned_flag = (
            owned if type(owned) is bool else
            (default_collection == "library" or collection == "library")
            if owned is None else False
        )
        downloadable_flag = downloadable if type(downloadable) is bool else None
        price, currency = self._price(item)
        cover = self.cover_url(item)
        metadata = {k: v for k, v in item.items() if k not in {"description"}}
        # SearchResult.metadata also reaches public details and diagnostics.
        # Replace (or remove) malformed author/language fields rather than
        # retaining the raw nested objects that the byline just rejected.
        if "authors" in metadata:
            if type(authors) in (list, tuple):
                metadata["authors"] = list(accepted_authors)
            else:
                metadata.pop("authors")
        if "author" in metadata and type(item.get("author")) is not str:
            metadata.pop("author")
        if "language" in metadata:
            raw_language = metadata["language"]
            if type(raw_language) is not str:
                metadata.pop("language")
            else:
                try:
                    canonical = normalize_language_tag(raw_language)
                    metadata["language"] = canonical if type(canonical) is str else None
                except Exception:
                    # An upstream invalid tag or failing normalizer must not
                    # abort all other catalogue hits or export its exception.
                    metadata["language"] = None
        if "languages" in metadata:
            raw_languages = metadata["languages"]
            if type(raw_languages) not in (list, tuple):
                metadata.pop("languages")
            else:
                accepted_languages: list[str] = []
                for raw in raw_languages:
                    if type(raw) is not str:
                        continue
                    try:
                        canonical = normalize_language_tag(raw)
                    except Exception:
                        continue
                    if type(canonical) is str and canonical not in accepted_languages:
                        accepted_languages.append(canonical)
                metadata["languages"] = accepted_languages
        if cover and collection == "catalog":
            metadata["catalog_cover"] = {
                "url": cover,
                "source": "kobo.catalog",
                "evidence_id": f"kobo:{product_id}",
                "evidence_strength": 100,
            }
        return SearchResult(
            id=f"kobo:{self.credential_id}:{product_id}", record_id=product_id, docbase="KOBO",
            medium="digital", title=str(item.get("title") or "Sans titre"), author=author,
            type="Livre numérique Kobo", format=fmt,
            publisher=str(item.get("publisher") or "").strip() or None,
            published_at=str(item.get("publication_date") or item.get("published_at") or "").strip() or None,
            description=description_text(item.get("description")),
            thumbnail_url=cover, friendly_url=str(item.get("product_url") or "").strip() or None,
            scenario="KOBO", source_provider=self.provider_id, source_credential=self.credential_id,
            download_url=None, content_type=content_type, source_collection=collection,
            owned=owned_flag,
            downloadable=downloadable_flag,
            price=price, currency=currency,
            revision_id=str(item.get("revision_id") or "").strip() or None,
            metadata=metadata,
        )

    def search(
        self, query: str, *, field: str = "all", limit: int = 50,
        page: int = 0, scope: str = "all",
    ) -> list[SearchResult]:
        self.last_search_rejections = []
        if scope not in {"catalog", "all"}:
            raise ProviderError("Kobo-Vault : portée de recherche invalide")
        try:
            payload = self.client.search(query, field=field, scope=scope, limit=limit, page=page)
        except KoboVaultError as exc:
            # Vault may authorize catalogue discovery yet refuse the separate
            # private /library/search route (e.g. missing delegated scope).
            # That denial must not make public discovery unavailable. Never
            # downgrade transport, throttling, or server failures to success.
            if scope != "all" or exc.status_code not in (401, 403):
                raise
            # A public-only request contains no evidence of account rights.
            # Apply the provider's public projection at this boundary as well,
            # even if an alternate client returns private-looking flags.
            payload = KoboVaultClient._public_catalog_payload(
                self.client.search(
                    query, field=field, scope="catalog", limit=limit, page=page
                )
            )
            self.last_search_rejections.append({
                "source": "library",
                "type": "invalid_scope",
                "reason": "personal_search_denied",
            })
        self.last_search_rejections.extend(
            dict(row)
            for row in getattr(self.client, "last_search_merge_rejections", ())
            if isinstance(row, dict)
        )
        if scope == "catalog":
            # Public-only projection is obligatory even with a substitute
            # client that returns forged personal rights on /catalog/search.
            payload = KoboVaultClient._public_catalog_payload(payload)
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        rows: list[SearchResult] = []
        for raw in items:
            if not isinstance(raw, dict):
                self.last_search_rejections.append({
                    "type": "invalid_shape",
                    "reason": "item_not_object",
                })
                continue
            try:
                rows.append(self.normalize_item(raw))
            except ProviderError:
                reason = (
                    "missing_product_id"
                    if _strict_product_id(raw) is None
                    else "normalization_error"
                )
                self.last_search_rejections.append({
                    "type": "ProviderError",
                    "reason": reason,
                })
        return rows


class KoboCatalogPlugin:
    """Plugin-visible factory used by provider.kobo.

    It intentionally does not create a provider session of its own: credentials
    belong to the personal-library connection, while catalogue behaviour is a
    separately replaceable plugin dependency.
    """
    def create(self, client: Any, *, credential_id: str, provider_id: str = "kobo") -> KoboCatalogService:
        return KoboCatalogService(client, credential_id=credential_id, provider_id=provider_id)


PLUGIN = KoboCatalogPlugin()
