from __future__ import annotations
from typing import Any
from ipaddress import ip_address
import re
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
            # A cover can be fetched by the UI without an explicit user click.
            # Never emit signed links, local/private destinations or credentials
            # from untrusted catalogue/personal-manifest JSON.
            if type(value) is not str or not 1 <= len(value) <= 4096:
                return None
            if (
                value != value.strip()
                or any(ord(ch) < 33 or ord(ch) == 127 for ch in value)
                or any(ch in value for ch in '<>\\')
            ):
                return None
            try:
                raw = urlsplit(value)
                if raw.query or raw.fragment:
                    return None
                if not raw.scheme and not raw.netloc and '/' not in raw.path and '.' not in raw.path:
                    return None
                if raw.scheme and raw.scheme.lower() not in {'http', 'https'}:
                    return None
                base = urlsplit(self.client.base_url)
                resolved = urljoin(self.client.base_url.rstrip('/') + '/', value)
                parsed = urlsplit(resolved)
                hostname = parsed.hostname
                port = parsed.port
                base_port = base.port
            except (ValueError, TypeError):
                return None
            # A user may explicitly configure a local Kobo Vault on loopback
            # or LAN. Loading a cover from that *same exact API origin* is
            # intentional, unlike following a URL supplied by a book record
            # to a different private host or port.
            trusted_vault_origin = (
                parsed.scheme == base.scheme
                and hostname == base.hostname
                and port == base_port
                and base.username is None and base.password is None
                and not base.query and not base.fragment
            )
            if (
                parsed.scheme not in {'http', 'https'}
                or not hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or (
                    not trusted_vault_origin
                    and port not in (None, 443 if parsed.scheme == 'https' else 80)
                )
                or re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f)', parsed.path, re.I)
            ):
                return None
            host = hostname.rstrip('.').casefold()
            if not trusted_vault_origin:
                if (
                    '.' not in host or '%' in host
                    or host in {'localhost', 'local', 'internal'}
                    or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home.arpa'))
                ):
                    return None
                try:
                    address = ip_address(host)
                except ValueError:
                    pass
                else:
                    if not address.is_global:
                        return None
            return resolved
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
    def _public_product_url(value: Any) -> str | None:
        """Expose only ordinary public product pages, never private URLs.

        Vault can return acquisition or signed-license destinations in a
        product_url field. The URI appears both as a clickable friendly_url
        and inside SearchResult.metadata, so both surfaces must be sanitized.
        """
        if type(value) is not str or not 1 <= len(value) <= 2048:
            return None
        if (
            value != value.strip()
            or any(ord(ch) < 33 or ord(ch) == 127 for ch in value)
            or any(ch in value for ch in '<>\\')
        ):
            return None
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {'https', 'http'}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or '%' in host
            or port not in (None, 443 if parsed.scheme == 'https' else 80)
        ):
            return None
        hostname = host.rstrip('.').casefold()
        if (
            '.' not in hostname
            or hostname in {'localhost', 'local', 'internal'}
            or hostname.endswith(
                ('.localhost', '.local', '.internal', '.lan', '.home.arpa')
            )
            or re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f)', parsed.path, re.I)
        ):
            return None
        try:
            address = ip_address(hostname)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None
        # Download/license/acquisition endpoints are not bibliographic
        # product notices even if hosted on an otherwise public domain.
        segments = [segment.casefold() for segment in parsed.path.split('/')]
        if any(segment in {'download', 'downloads', 'license', 'licence', 'acquisition', 'authorize', 'oauth', 'token'} for segment in segments):
            return None
        return value

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
        authors = item.get("authors")
        if isinstance(authors, list):
            author = ", ".join(str(x) for x in authors if str(x).strip()) or None
        else:
            author = str(item.get("author") or "").strip() or None
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
        # The normalized cover URL is the only cover reference safe to export.
        # Dropping raw nested variants prevents an unsafe fallback (including
        # query tokens) from surviving in SearchResult.metadata.
        cover_keys = {
            "cover_url", "thumbnail_url", "image_url", "cover", "image",
            "images", "covers", "CoverImageUrl", "ImageUrl",
        }
        metadata = {
            k: v for k, v in item.items()
            if k != "description" and k not in cover_keys
        }
        if cover:
            metadata["cover_url"] = cover
        product_url = self._public_product_url(item.get("product_url"))
        if product_url is None:
            metadata.pop("product_url", None)
        else:
            metadata["product_url"] = product_url
        if isinstance(metadata.get("language"), str):
            metadata["language"] = normalize_language_tag(metadata["language"])
        if isinstance(metadata.get("languages"), (list, tuple)):
            metadata["languages"] = list(dict.fromkeys(
                canonical for raw in metadata["languages"]
                if (canonical := normalize_language_tag(str(raw))) is not None
            ))
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
            thumbnail_url=cover, friendly_url=product_url,
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
