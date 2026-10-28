from __future__ import annotations
from typing import Any, Callable
from ipaddress import ip_address
import re
from urllib.parse import unquote, urljoin, urlsplit

from cifereca.plugin_api import ProviderError, SearchResult, normalize_language_tag, description_text, description_element
from cifereca_kobo_policy import _strict_product_id
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


def _unsafe_encoded_cover_path(path: str) -> bool:
    """Reject controls hidden under repeated URI decoding, without network I/O."""
    for _ in range(5):
        if any(ord(char) < 32 or ord(char) == 127 for char in path):
            return True
        decoded = unquote(path)
        if decoded == path:
            return False
        path = decoded
    # An excessively wrapped path has not been fully inspected.
    return True


def _unsafe_cover_resource_path(path: str) -> bool:
    """Only image-like paths cross the public, auto-loaded thumbnail boundary.

    urljoin() normalizes ordinary '..' segments, so inspect the *original*
    source path before joining, including repeatedly percent-encoded slashes.
    Do not rewrite the URL or accept partially decoded opaque paths.
    """
    private_segments = {
        "acquire", "acquisition", "download", "downloads", "fulfillment",
        "license", "licence", "licenses", "licences", "oauth", "token",
        "tokens", "authorization", "authorize", "delegate", "drm", "adept",
    }
    for _ in range(5):
        segments = (
            segment.casefold() for segment in path.replace("\\", "/").split("/")
            if segment
        )
        if any(
            segment == ".." or segment in private_segments
            or segment.endswith((".acsm", ".lcpl", ".epub", ".kepub", ".pdf"))
            for segment in segments
        ):
            return True
        decoded = unquote(path)
        if decoded == path:
            return False
        path = decoded
    return True


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
                if raw.query or raw.fragment or _unsafe_cover_resource_path(raw.path):
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
                or _unsafe_encoded_cover_path(parsed.path)
                or _unsafe_cover_resource_path(parsed.path)
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
                    # Some URL clients resolve shorthand IPv4 notations
                    # (127.1, 0177.0.0.1, 192.168.1) to private networks,
                    # while ipaddress() rejects their noncanonical spelling.
                    # A numeric final DNS label is not a public hostname.
                    if host.rsplit(".", 1)[-1].isdigit():
                        return None
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
            or len(hostname) > 253
            or any(
                not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in hostname.split('.')
            )
        ):
            return None
        try:
            address = ip_address(hostname)
        except ValueError:
            address = None
        if address is not None:
            if not address.is_global:
                return None
        elif not re.fullmatch(r'[a-z][a-z0-9-]{1,62}', hostname.rsplit('.', 1)[-1]):
            # Noncanonical numeric hosts such as 127.1 and 0177.0.0.1
            # are not harmless public DNS hostnames.
            return None
        # Decode solely to reject acquisition/license endpoints concealed
        # as percent-encoded URL paths. The returned URL is never rewritten.
        path = parsed.path
        private_segments = {
            'download', 'downloads', 'license', 'licence', 'licenses',
            'licences', 'acquisition', 'acquire', 'authorize',
            'authorization', 'oauth', 'token', 'tokens', 'login',
            'signin', 'drm', 'adept',
        }
        for _ in range(4):
            if any(ord(char) < 32 or ord(char) == 127 for char in path):
                return None
            segments = [part.casefold() for part in path.split('/') if part]
            if any(
                part in private_segments
                or part.endswith(('.acsm', '.lcpl', '.epub', '.kepub'))
                for part in segments
            ):
                return None
            decoded = unquote(path)
            if decoded == path:
                return value
            path = decoded
        # Deeply encoded routes have not been fully inspected: fail closed.
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
            elif not raw_language.strip():
                metadata["language"] = None
            else:
                try:
                    canonical = normalize_language_tag(raw_language)
                    metadata["language"] = (
                        canonical if type(canonical) is str and canonical.strip() else None
                    )
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
                    if type(raw) is not str or not raw.strip():
                        continue
                    try:
                        canonical = normalize_language_tag(raw)
                    except Exception:
                        continue
                    if (
                        type(canonical) is str and canonical.strip()
                        and canonical not in accepted_languages
                    ):
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
        private_guard: Callable[[], None] | None = None,
    ) -> list[SearchResult]:
        self.last_search_rejections = []
        if scope not in {"catalog", "all"}:
            raise ProviderError("Kobo-Vault : portée de recherche invalide")
        # Only the actual Vault client understands per-network-hop owner
        # guards; injected old catalogue clients keep their v1 method shape.
        guarded_options = (
            {"private_guard": private_guard}
            if private_guard is not None and isinstance(self.client, KoboVaultClient)
            else {}
        )
        try:
            payload = self.client.search(
                query, field=field, scope=scope, limit=limit, page=page,
                **guarded_options,
            )
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
        # Diagnostics from the Vault client cross into Core and may be
        # exported to users. Retain only the finite, already-attested public
        # vocabulary, not arbitrary private fields/vendor error strings.
        merge_rejections = getattr(self.client, "last_search_merge_rejections", ())
        if type(merge_rejections) in (tuple, list):
            for row in merge_rejections[:128]:
                if type(row) is not dict:
                    continue
                source = row.get("source")
                kind = row.get("type")
                reason = row.get("reason")
                if (type(source) is not str or source not in {"catalog", "library"}
                        or type(kind) is not str
                        or kind not in {"invalid_shape", "invalid_scope"}):
                    continue
                known_reasons = {
                    "item_not_object", "items_not_list", "missing_product_id",
                    "unverifiable_product_id", "duplicate_product_id",
                    "tainted_product_id",
                }
                self.last_search_rejections.append({
                    "source": source,
                    "type": kind,
                    "reason": (
                        reason if type(reason) is str and reason in known_reasons
                        else "unverified_rejection"
                    ),
                })
        if scope == "catalog":
            # Public-only projection is obligatory even with a substitute
            # client that returns forged personal rights on /catalog/search.
            payload = KoboVaultClient._public_catalog_payload(payload)
        # A malformed success envelope is not evidence of a valid zero.
        # The real Vault may reject one branch and still return other rows;
        # absent/non-array items after projection cannot be called empty.
        if type(payload) is not dict or type(payload.get("items")) is not list:
            raise ProviderError("Kobo-Vault : invalid_search_response")
        items = payload["items"]
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
            except Exception:
                # A malformed scalar or custom mapping may throw while
                # normalizing a single row (e.g. __str__/__bool__ on a
                # provider value). Keep other safe books searchable, but
                # never echo its private exception text or class name.
                reason = "normalization_error"
                try:
                    if type(raw) is dict and _strict_product_id(raw) is None:
                        reason = "missing_product_id"
                except Exception:
                    pass
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
