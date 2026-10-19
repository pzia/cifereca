"""BnF Prêt numérique — fail-closed OPDS 2.0 feed contract (offline only).

The existence of /v1/home.opds2 does not establish public anonymous access,
SSO compatibility, or borrowing permission. This module performs NO network IO
and is intentionally not registered as a provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

HOME_URL = "https://pret.bnf.fr/v1/home.opds2"
FEED_TYPE = "application/opds+json"
SAFE_QUERY_KEYS = frozenset({
    "page", "query", "q", "title", "author", "sort", "category", "filter", "language"
})
MAX_ENTRIES = 1000


class OPDS2ContractError(ValueError):
    """Only emits a stable code, never server-supplied text or signed URLs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _is_feed_type(value: Any) -> bool:
    return isinstance(value, str) and value.split(";", 1)[0].strip().lower() == FEED_TYPE


def _safe_link(raw: Any, *, base: str = HOME_URL) -> str | None:
    """Validate the entire navigation URL; never reflect arbitrary upstream URLs."""
    if not isinstance(raw, str) or len(raw) > 2048 or "{" in raw or "}" in raw:
        return None
    try:
        resolved = urljoin(base, raw)
        parts = urlsplit(resolved)
        query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False,
                          max_num_fields=30)
        if (parts.scheme != "https" or parts.hostname != "pret.bnf.fr"
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment
                or not parts.path.startswith("/v1/")
                or "\\" in parts.path or any(p in {".", ".."} for p in parts.path.split("/"))
                or any(key not in SAFE_QUERY_KEYS or not value or len(value) > 256
                       for key, value in query)):
            return None
        return urlunsplit(("https", "pret.bnf.fr", parts.path,
                           urlencode(query), ""))
    except (ValueError, UnicodeError):
        return None


def _str(value: Any, *, max_length: int = 512) -> str | None:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= max_length:
        return None
    value = value.strip()
    # Avoid an HTML title being interpreted as markup by a consumer.
    if any(char in value for char in "<>\x00\r\n"):
        return None
    return value


def _name(value: Any) -> str | None:
    if isinstance(value, str):
        return _str(value)
    if isinstance(value, Mapping):
        return _str(value.get("name"))
    if isinstance(value, list):
        return ", ".join(names) if (names := [n for item in value if (n := _name(item))]) else None
    return None


def _rels(value: Any) -> frozenset[str]:
    values = (value,) if isinstance(value, str) else value if isinstance(value, list) else ()
    return frozenset(item for item in values if isinstance(item, str))


@dataclass(frozen=True)
class Navigation:
    title: str
    url: str


@dataclass(frozen=True)
class Publication:
    title: str
    identifier: str | None
    author: str | None
    publisher: str | None
    cover_url: str | None
    # A catalog entry is never an entitlement or a personal loan.
    owned: bool = False
    borrowable: bool = False


@dataclass(frozen=True)
class Catalog:
    title: str
    navigation: tuple[Navigation, ...]
    publications: tuple[Publication, ...]
    next_url: str | None
    search_template: str | None
    authentication_advertised: bool = False


def _catalog_navigation(raw: Any, *, base: str) -> list[Navigation]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise OPDS2ContractError("invalid_navigation")
    items = []
    for link in raw:
        if not isinstance(link, Mapping):
            raise OPDS2ContractError("invalid_navigation")
        title = _str(link.get("title"))
        url = _safe_link(link.get("href"), base=base) if _is_feed_type(link.get("type")) else None
        if title and url:
            items.append(Navigation(title, url))
    return items


def _safe_cover(value: Any, *, base: str) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        url = urlsplit(urljoin(base, value))
        if (url.scheme != "https" or url.username is not None or url.password is not None
                or url.port not in (None, 443) or url.query or url.fragment
                or "\\" in url.path or any(s in {".", ".."} for s in url.path.split("/"))):
            return None
        if url.hostname == "assets.cantook.net" and url.path.startswith("/assets/"):
            return urlunsplit(("https", url.hostname, url.path, "", ""))
        if url.hostname == "pret.bnf.fr" and url.path.startswith("/assets/"):
            return urlunsplit(("https", url.hostname, url.path, "", ""))
    except (ValueError, UnicodeError):
        return None
    return None


def _catalog_publications(raw: Any, *, base: str) -> list[Publication]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise OPDS2ContractError("invalid_publications")
    result = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("metadata"), Mapping):
            raise OPDS2ContractError("invalid_publication")
        meta = item["metadata"]
        title = _str(meta.get("title"))
        if not title:
            continue
        images = item.get("images", [])
        if not isinstance(images, list) or len(images) > MAX_ENTRIES:
            raise OPDS2ContractError("invalid_images")
        cover = next((url for image in images if isinstance(image, Mapping)
                      and isinstance(image.get("type"), str)
                      and image["type"].lower() in {"image/jpeg", "image/png", "image/webp"}
                      if (url := _safe_cover(image.get("href"), base=base))), None)
        identifier = _str(meta.get("identifier"))
        result.append(Publication(
            title=title, identifier=identifier, author=_name(meta.get("author")),
            publisher=_name(meta.get("publisher")), cover_url=cover))
    return result


def _search_template(raw: Any, *, base: str) -> str | None:
    """Accept only a known safe RFC6570 simple query expansion for OPDS search."""
    if not isinstance(raw, str) or len(raw) > 2048 or raw.count("{") != 1:
        return None
    prefix, sep, expression = raw.partition("{")
    if not sep or not expression.endswith("}") or "}" in prefix:
        return None
    expression = expression[:-1]
    if not expression.startswith("?"):
        return None
    keys = expression[1:].split(",")
    if (not keys or "query" not in keys or len(set(keys)) != len(keys)
            or any(key not in SAFE_QUERY_KEYS for key in keys)):
        return None
    return prefix + "{?" + ",".join(keys) + "}" if _safe_link(prefix, base=base) else None


def expand_search(template: str, query: str, *, base: str = HOME_URL) -> str:
    """Expands validated search templates without making HTTP requests."""
    validated = _search_template(template, base=base)
    value = _str(query, max_length=256)
    if validated is None or value is None:
        raise OPDS2ContractError("invalid_search")
    prefix, expression = validated.split("{?", 1)
    keys = expression[:-1].split(",")
    resolved = _safe_link(prefix, base=base)
    if not resolved:
        raise OPDS2ContractError("invalid_search")
    parts = urlsplit(resolved)
    params = parse_qsl(parts.query, keep_blank_values=False)
    if "query" not in keys:
        raise OPDS2ContractError("invalid_search")
    params.append(("query", value))
    return urlunsplit(("https", "pret.bnf.fr", parts.path, urlencode(params), ""))


def parse_catalog(payload: Any, *, url: str = HOME_URL, status: int = 200,
                  content_type: str = FEED_TYPE) -> Catalog:
    """Parse navigation / groups / pages. Never follows auth or acquisition links."""
    if status in {401, 403}:
        raise OPDS2ContractError("auth_required" if status == 401 else "access_forbidden")
    if status == 429:
        raise OPDS2ContractError("rate_limited")
    if status >= 500:
        raise OPDS2ContractError("upstream_unavailable")
    if status != 200:
        raise OPDS2ContractError("unexpected_status")
    if not _is_feed_type(content_type):
        raise OPDS2ContractError("invalid_content_type")
    base = _safe_link(url)
    if not base or not isinstance(payload, Mapping):
        raise OPDS2ContractError("invalid_catalog")
    meta = payload.get("metadata")
    if not isinstance(meta, Mapping) or not (title := _str(meta.get("title"))):
        raise OPDS2ContractError("invalid_catalog")
    links = payload.get("links")
    if not isinstance(links, list) or len(links) > MAX_ENTRIES:
        raise OPDS2ContractError("invalid_links")
    navigation = _catalog_navigation(payload.get("navigation"), base=base)
    publications = _catalog_publications(payload.get("publications"), base=base)
    groups = payload.get("groups", [])
    if not isinstance(groups, list) or len(groups) > MAX_ENTRIES:
        raise OPDS2ContractError("invalid_groups")
    for group in groups:
        if not isinstance(group, Mapping):
            raise OPDS2ContractError("invalid_group")
        navigation.extend(_catalog_navigation(group.get("navigation"), base=base))
        publications.extend(_catalog_publications(group.get("publications"), base=base))
    if len(navigation) + len(publications) > MAX_ENTRIES:
        raise OPDS2ContractError("too_many_entries")
    next_url = None
    search = None
    auth = False
    for link in links:
        if not isinstance(link, Mapping):
            raise OPDS2ContractError("invalid_link")
        rel = _rels(link.get("rel"))
        auth |= "authenticate" in rel or "http://opds-spec.org/authenticate" in rel
        if "next" in rel and _is_feed_type(link.get("type")):
            next_url = _safe_link(link.get("href"), base=base)
        if "search" in rel and _is_feed_type(link.get("type")) and link.get("templated") is True:
            search = _search_template(link.get("href"), base=base)
    if not (navigation or publications):
        raise OPDS2ContractError("empty_catalog")
    return Catalog(title, tuple(navigation), tuple(publications), next_url, search, auth)
