"""Bounded, offline projections of the reader portal DOM (#2055).

No HTTP, browser state, SAML, mutation or acquisition. Outputs are private
application data, never diagnostic payloads. Synthetic selectors are documented
in WEB.md; a successful parse does not authenticate a session or attest a right.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

ORIGIN = "https://pret.bnf.fr"
MAX_HTML_BYTES = 512 * 1024
MAX_NODES = 12000
MAX_DEPTH = 64
MAX_ITEMS = 100
MAX_TEXT = 4096
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_HIDDEN = frozenset({"script", "style", "template", "noscript"})


class WebParseError(ValueError):
    """Closed error codes only; never interpolate a page or caller value."""


@dataclass(repr=False)
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list = field(default_factory=list)
    closed: bool = False

    def walk(self):
        if self.tag in _HIDDEN or "hidden" in self.attrs or self.attrs.get("aria-hidden", "").strip().lower() == "true":
            return
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def text(self):
        if self.tag in _HIDDEN or "hidden" in self.attrs or self.attrs.get("aria-hidden", "").strip().lower() == "true":
            return ""
        result = " ".join(c.text() if isinstance(c, _Node) else c for c in self.children)
        return " ".join(result.split())[:MAX_TEXT]


class _DOM(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > MAX_NODES or len(self.stack) >= MAX_DEPTH:
            raise WebParseError("dom_limit")
        # Duplicate attributes have browser-dependent semantics. Refuse them.
        if len({k for k, _ in attrs}) != len(attrs):
            raise WebParseError("invalid_dom")
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)
        else:
            node.closed = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack[i].closed = True
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _dom(html: str) -> _Node:
    if not isinstance(html, str):
        raise WebParseError("invalid_html")
    # Character bound before allocating the UTF-8 encoding.
    if len(html) > MAX_HTML_BYTES:
        raise WebParseError("html_limit")
    try:
        size = len(html.encode("utf-8"))
    except UnicodeError:
        raise WebParseError("invalid_html") from None
    if size > MAX_HTML_BYTES:
        raise WebParseError("html_limit")
    parser = _DOM()
    parser.feed(html)
    parser.close()
    root = parser.root
    # Never inspect a login assertion or infer an authenticated account from it.
    if any(n.attrs.get("name", "").lower() in {"samlresponse", "password"}
           or n.attrs.get("type", "").lower() == "password" for n in root.walk()):
        raise WebParseError("auth_required")
    return root


def _id(href: str, kind: str) -> str | None:
    """Extract a route identifier, discarding every query/fragment and URL."""
    if not href or len(href) > 8192 or any(ord(c) <= 32 or c == "\\" for c in href):
        return None
    try:
        u = urlsplit(href)
        if u.scheme or u.netloc:
            if u.scheme != "https" or u.netloc != "pret.bnf.fr":
                return None
        match = re.fullmatch(r"/" + kind + r"/([A-Za-z0-9_-]{1,128})/?", u.path)
    except ValueError:
        return None
    return match.group(1) if match else None


def _links(node, kind):
    return {value for n in node.walk() if n.tag == "a"
            and (value := _id(n.attrs.get("href", ""), kind))}


@dataclass(frozen=True)
class CatalogRecord:
    resource_id: str = field(repr=False)
    title: str = field(repr=False)
    authors: tuple[str, ...] = field(default=(), repr=False)
    publisher: str | None = field(default=None, repr=False)
    isbn: str | None = field(default=None, repr=False)
    language: str | None = field(default=None, repr=False)
    description: str | None = field(default=None, repr=False)
    # UI button presence is not an availability or borrowing capability.
    availability: str = "unknown"
    # Public image belongs only to this exact, observed catalogue record.
    thumbnail_url: str | None = field(default=None, repr=False)
    # Only a complete native results card, not suggestions/detail fallback,
    # can attest the exact catalogue resource coordinate for this tranche.
    web_reference_evidence: bool = False


@dataclass(frozen=True)
class CatalogPage:
    records: tuple[CatalogRecord, ...]
    status: str
    scope: str = "suggestions"
    exhaustive: bool = False


def parse_suggestions(html: str) -> CatalogPage:
    root = _dom(html)
    lists = [n for n in root.walk() if n.has_class("search-results__list")]
    if len(lists) != 1 or not lists[0].closed:
        raise WebParseError("unrecognized_search")
    cards = [n for n in lists[0].walk() if n.has_class("search-resource-result")]
    if len(cards) > MAX_ITEMS:
        raise WebParseError("item_limit")
    records = {}
    partial = False
    for card in cards:
        if not card.closed:
            partial = True
            continue
        ids = _links(card, "resources")
        if len(ids) != 1:
            partial = True
            continue
        rid = next(iter(ids))
        names = _properties(card).get("name", ())
        # Link text is an explicitly weaker fallback for suggestion fragments.
        titles = names or tuple(n.text() for n in card.walk() if n.tag == "a"
                                and _id(n.attrs.get("href", ""), "resources") == rid and n.text())
        titles = tuple(dict.fromkeys(titles))
        if len(titles) != 1:
            partial = True
            continue
        props = _properties(card)
        def single(key):
            values = props.get(key, ())
            return values[0] if len(values) == 1 else None
        partial |= any(len(props.get(key, ())) > 1
                       for key in ('publisher', 'isbn', 'inLanguage', 'description'))
        record = CatalogRecord(rid, titles[0], tuple(props.get('author', ())),
                               single('publisher'), single('isbn'), single('inLanguage'),
                               single('description'), thumbnail_url=_loan_cover(card, rid))
        if rid in records and records[rid] != record:
            partial = True
            if records[rid].thumbnail_url != record.thumbnail_url:
                # A duplicate must not select the first or last cover.
                records[rid] = replace(records[rid], thumbnail_url=None)
        else:
            records[rid] = record
    return CatalogPage(tuple(records.values()), "partial" if partial else "ok")



def parse_search_page(html: str) -> CatalogPage:
    """Search actual full catalogue, retaining legacy suggestions compatibility.

    The observed /resources?q= page has a ul.list-details holding
    li.list-details__item with an article.view-details Book microdata scope.
    Autocomplete search-results__list is distinct from the full catalogue.
    """
    root = _dom(html)
    lists = [n for n in root.walk() if n.tag == "ul" and n.has_class("list-details")]
    if not lists:
        return parse_suggestions(html)
    if len(lists) != 1 or not lists[0].closed:
        raise WebParseError("unrecognized_search")
    cards = [n for n in lists[0].walk()
             if n.tag == "li" and n.has_class("list-details__item")]
    if not cards:
        # No real zero-results DOM has been observed; do not manufacture it.
        raise WebParseError("unrecognized_search")
    if len(cards) > MAX_ITEMS:
        raise WebParseError("item_limit")
    records = {}
    partial = False
    for card in cards:
        if not card.closed:
            partial = True
            continue
        books = [n for n in card.walk()
                 if n.tag == "article" and n.has_class("view-details")
                 and "itemscope" in n.attrs
                 and any(t in {"http://schema.org/Book", "https://schema.org/Book"}
                         for t in n.attrs.get("itemtype", "").split())]
        if len(books) != 1 or not books[0].closed:
            partial = True
            continue
        book = books[0]
        title_links = []
        for link in book.walk():
            if link.tag != "a":
                continue
            rid = _id(link.attrs.get("href", ""), "resources")
            if rid is None:
                continue
            for heading in link.walk():
                if (heading.tag == "h3" and heading.closed
                        and heading.has_class("details-content__title")
                        and heading.text()):
                    title_links.append((rid, heading.text()))
        if len(title_links) != 1:
            partial = True
            continue
        rid, title = title_links[0]
        if _links(book, "resources") != {rid}:
            partial = True
            continue

        props = _properties(book)
        descriptions = [n.text() for n in book.walk()
                        if n.tag == "p" and n.closed
                        and n.has_class("details-content__body") and n.text()]
        def single(key):
            values = props.get(key, ())
            return values[0] if len(values) == 1 else None
        if (len(descriptions) > 1 or
                any(len(props.get(k, ())) > 1
                    for k in ("publisher", "isbn", "inLanguage"))):
            partial = True
        record = CatalogRecord(
            rid, title, tuple(props.get("author", ())),
            single("publisher"), single("isbn"), single("inLanguage"),
            descriptions[0] if len(descriptions) == 1 else None,
            thumbnail_url=_catalog_book_cover(book, rid),
            web_reference_evidence=True,
        )
        if rid in records and records[rid] != record:
            partial = True
            if records[rid].thumbnail_url != record.thumbnail_url:
                records[rid] = replace(records[rid], thumbnail_url=None)
        else:
            records[rid] = record
    if not records:
        raise WebParseError("unrecognized_search")
    # A conflicting or malformed occurrence anywhere on the returned
    # page is not a complete native reference inventory. Keep the ordinary
    # bibliographic rows, but do not issue an authenticated web proof.
    if partial:
        records = {rid: replace(record, web_reference_evidence=False)
                   for rid, record in records.items()}
    return CatalogPage(tuple(records.values()), "partial" if partial else "ok",
                       scope="results")



def _properties(root):
    result = {}
    allowed = {"name", "author", "publisher", "isbn", "inLanguage", "description"}
    def nodes(node):
        if node.tag in _HIDDEN or "hidden" in node.attrs or node.attrs.get("aria-hidden", "").strip().lower() == "true":
            return
        yield node
        # Nested Person/Organization/Book names aren't the parent Book name.
        if node is not root and "itemscope" in node.attrs:
            return
        for child in node.children:
            if isinstance(child, _Node):
                yield from nodes(child)
    for node in nodes(root):
        if node.tag in _HIDDEN:
            continue
        for prop in node.attrs.get("itemprop", "").split():
            if prop in allowed:
                value = node.attrs.get("content", node.text()).strip()[:MAX_TEXT]
                if value and value not in result.setdefault(prop, []):
                    result[prop].append(value)
    return result


def parse_detail(html: str, *, page_url: str) -> CatalogPage:
    rid = _id(page_url, "resources")
    if rid is None:
        raise WebParseError("invalid_resource_page")
    root = _dom(html)
    # Only microdata within a Book scope; header/account names aren't book names.
    books = [n for n in root.walk() if "itemscope" in n.attrs
             and any(t in {"http://schema.org/Book", "https://schema.org/Book"}
                     for t in n.attrs.get("itemtype", "").split())]
    if len(books) != 1 or not books[0].closed:
        raise WebParseError("unrecognized_detail")
    props = _properties(books[0])
    if len(props.get("name", ())) != 1:
        raise WebParseError("ambiguous_detail")
    partial = any(len(props.get(k, ())) > 1 for k in ("publisher", "isbn", "inLanguage", "description"))
    def single(key):
        values = props.get(key, ())
        return values[0] if len(values) == 1 else None
    # Only a closed, visible marker in this Book's own scope is evidence.
    # A nested recommendation/organization or hidden template is not.
    def offer_nodes(node):
        style = "".join(node.attrs.get("style", "").lower().split())
        if (not node.closed or node.tag in _HIDDEN or "hidden" in node.attrs
                or node.attrs.get("aria-hidden", "").strip().lower() == "true"
                or "display:none" in style or "visibility:hidden" in style
                or (node is not books[0] and "itemscope" in node.attrs)):
            return
        yield node
        for child in node.children:
            if isinstance(child, _Node):
                yield from offer_nodes(child)

    unavailable = any(n.has_class("lb_unavailable") and n.text()
                      for n in offer_nodes(books[0]))
    record = CatalogRecord(rid, props["name"][0], tuple(props.get("author", ())),
                           single("publisher"), single("isbn"), single("inLanguage"),
                           single("description"), "unavailable" if unavailable else "unknown",
                           _catalog_book_cover(books[0], rid))
    return CatalogPage((record,), "partial" if partial else "ok", scope="detail")


@dataclass(frozen=True)
class ProfileScope:
    identity_id: str = field(repr=False)
    account_id: str = field(repr=False)
    session_epoch: str = field(repr=False)

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 256
               for v in (self.identity_id, self.account_id, self.session_epoch)):
            raise WebParseError("invalid_scope")



def _public_cover_url(value: str) -> str | None:
    """A narrowly validated public image URL, never a loan or SSO endpoint.

    The page's image URL is not proof that it is this loan's cover:
    _loan_cover additionally requires an image nested in the exact resource
    link inside an observed loan card. No network request is made here.
    """
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(ord(char) <= 32 or ord(char) > 126 or char == '\\' for char in value)
            or not (value.startswith('https://') or
                    (value.startswith('/') and not value.startswith('//')))):
        return None
    try:
        # Reject traversal *before* urljoin normalizes dot segments.
        raw_path = urlsplit(value).path
        if any(segment in {'.', '..', ''} for segment in raw_path.split('/')[1:]):
            return None
        url = urljoin(ORIGIN + '/', value)
        parts = urlsplit(url)
        # BnF's real catalogue/loan cards link to public Feedbooks covers.
        # This is a dedicated, closed URL family, NOT a general permission
        # for queries on loan/SSO paths or the other image hosts.
        if parts.netloc == 'covers.feedbooks.net':
            if (parts.scheme != 'https' or parts.username or parts.password
                    or parts.fragment
                    or not re.fullmatch(r'/item/[A-Za-z0-9_-]{1,128}\.jpg', parts.path)
                    or not re.fullmatch(
                        r'(?:size=[A-Za-z]{1,16}&t=[0-9]{1,20}|'
                        r't=[0-9]{1,20}&size=[A-Za-z]{1,16})',
                        parts.query,
                    )):
                return None
            return url
        if (parts.scheme != 'https' or parts.netloc not in
                {'pret.bnf.fr', 'imagedelivery.net'}
                or parts.username or parts.password or parts.query or parts.fragment
                or not re.fullmatch(r'/[A-Za-z0-9_./-]+', parts.path)
                or any(segment in {'.', '..', ''} for segment in parts.path.split('/')[1:])):
            return None
        if parts.netloc == 'pret.bnf.fr':
            if not re.fullmatch(
                r'/(?:assets|images?|covers?|media|uploads|sites|files|static)/'
                r'[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp)', parts.path,
                flags=re.IGNORECASE,
            ):
                return None
        elif not re.fullmatch(
            r'/[A-Za-z0-9_-]{8,128}/[A-Za-z0-9_-]{8,128}/'
            r'(?:public|default|thumbnail|cover|small|medium|large)', parts.path,
        ):
            return None
    except ValueError:
        return None
    return url


def _catalog_book_cover(book: _Node, resource_id: str | None = None) -> str | None:
    """A cover only within the validated, unique Book microdata scope.

    Do not consult global og:image, adjacent result cards, page header,
    profile avatars, nested Person/Organization scopes or a book title/ISBN.
    A conflicting or invalid image candidate means unknown, not first wins.
    """
    declared: list[str] = []
    unmarked: list[str] = []

    def visit(node: _Node) -> None:
        if (node.tag in _HIDDEN or 'hidden' in node.attrs
                or node.attrs.get('aria-hidden') == 'true'):
            return
        if node is not book and 'itemscope' in node.attrs:
            return  # Nested Book/Person/Organization isn't this resource.
        if node.tag == 'a' and resource_id is not None:
            linked = _id(node.attrs.get('href', ''), 'resources')
            if linked is not None and linked != resource_id:
                return  # Embedded recommendations cannot supply this cover.
        if node.tag == 'img':
            source = node.attrs.get('data-src') or node.attrs.get('src') or ''
            if node.has_class('cover') or 'image' in node.attrs.get('itemprop', '').split():
                declared.append(source)
            else:
                unmarked.append(source)
        elif node.tag == 'meta' and 'image' in node.attrs.get('itemprop', '').split():
            declared.append(node.attrs.get('content') or '')
        for child in node.children:
            if isinstance(child, _Node):
                visit(child)

    visit(book)
    # A declared Book cover outranks unrelated/decorative unmarked images.
    # Keep the old conservative fallback for legacy Book markup without
    # explicit microdata/class markers. Conflicting declared covers stay null.
    sources = declared if declared else unmarked
    return _cover_candidates_diagnosis(sources, limit=4)[1]


def _cover_candidates_diagnosis(
    sources: list[str], *, limit: int,
) -> tuple[str, str | None]:
    """Classify already-extracted image attributes, without HTTP or rights.

    A missing source, rejected/credential-bearing URL, conflicting public
    candidates and excessive candidate count are distinct observations.
    The only positive result is one identical, allowed public image URL.
    Callers must not promote statuses to loan eligibility or infer a match
    by book title/ISBN. The status is a fixed vocabulary; raw URLs stay
    inside the existing thumbnail field, not in the status.
    """
    if not sources:
        return "missing", None
    if len(sources) > limit:
        return "ambiguous", None
    images = [_public_cover_url(source) for source in sources]
    if any(image is None for image in images):
        return "rejected", None
    if len(set(images)) != 1:
        return "conflicting", None
    return "observed", images[0]


def _loan_cover_candidates(card: _Node, resource_id: str) -> list[str]:
    """Only attributes nested in the *exact* linked resource of this loan."""
    sources: list[str] = []
    for anchor in card.walk():
        if anchor.tag != 'a' or _id(anchor.attrs.get('href', ''), 'resources') != resource_id:
            continue
        for node in anchor.walk():
            if node.tag == 'img':
                sources.append(node.attrs.get('data-src') or node.attrs.get('src') or '')
    return sources


def _loan_cover_diagnosis(card: _Node, resource_id: str) -> tuple[str, str | None]:
    """Non-authorizing, in-memory classification of one exact loan card."""
    return _cover_candidates_diagnosis(
        _loan_cover_candidates(card, resource_id), limit=3,
    )


def _loan_cover(card: _Node, resource_id: str) -> str | None:
    """Only the exact resource link in this loan card can attest an image.

    A global image, profile avatar, homonymous catalogue image, or second
    conflicting cover stays unknown. Never use a title/ISBN fallback.
    """
    return _loan_cover_diagnosis(card, resource_id)[1]



# Qualified on the official BnF activity page (02/11/2026). Never use
# expiry from an ACSM, signed URL or the global quota renewal notice.
_MONTHS_FR = dict(zip(
    ("janvier février mars avril mai juin juillet août septembre octobre novembre décembre").split(),
    range(1, 13),
))
_DATE_FR = re.compile(r"([0-9]{1,2}) ([^\W\d_]+) ([0-9]{4}) à ([0-9]{2}):([0-9]{2})")


def _loan_due_at(card: _Node) -> str | None:
    """Strictly local wall-clock date on the validated loan's own card."""
    articles = [n for n in card.walk()
                if n.tag == "article" and n.has_class("js-loan__content")]
    if len(articles) != 1 or not articles[0].closed:
        return None
    article = articles[0]

    def owned(node: _Node):
        if node is not article and (
                node.tag == "article" or node.has_class("js-loan") or
                ("itemscope" in node.attrs and
                 node.attrs.get("itemtype", "").rstrip("/").endswith("/Book"))):
            return  # skip nested recommendations and foreign card/book scopes
        if node.tag in _HIDDEN or "hidden" in node.attrs or node.attrs.get("aria-hidden", "").strip().lower() == "true":
            return
        yield node
        for child in node.children:
            if isinstance(child, _Node):
                yield from owned(child)

    blocks = [child for n in owned(article) if n.has_class("details-content__body")
              for child in n.children if isinstance(child, _Node)
              and child.has_class("time-remaining") and child.closed
              and "hidden" not in child.attrs and child.attrs.get("aria-hidden") != "true"]
    if len(blocks) != 1:
        return None
    block = blocks[0]
    tags = [x for x in block.children if isinstance(x, _Node)]
    if len(tags) != 1 or tags[0].tag != "strong" or not tags[0].closed:
        return None
    strong = tags[0]
    if ("hidden" in strong.attrs or strong.attrs.get("aria-hidden", "").strip().lower() == "true"
            or any(isinstance(x, _Node) for x in strong.children)):
        return None
    i = block.children.index(strong)
    before = " ".join(" ".join(x.split()) for x in block.children[:i] if isinstance(x, str)).strip()
    after = " ".join(" ".join(x.split()) for x in block.children[i + 1:] if isinstance(x, str)).strip()
    if before.casefold() != "votre prêt expirera le" or after not in {"", "."}:
        return None
    match = _DATE_FR.fullmatch(strong.text())
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    month_number = _MONTHS_FR.get(month.casefold())
    if month_number is None:
        return None
    try:
        # No timezone is attested by the portal. Do not append Z/offset.
        return datetime(int(year), month_number, int(day),
                        int(hour), int(minute)).isoformat(timespec="seconds")
    except ValueError:
        return None


@dataclass(frozen=True)
class LoanRecord:
    loan_id: str = field(repr=False)
    resource_id: str = field(repr=False)
    title: str = field(default="", repr=False, compare=False)
    # Only Book/loan-card microdata can attest a public bibliographic author.
    # Do not use headers, linked catalogue names or unrelated book scopes.
    authors: tuple[str, ...] = field(default=(), repr=False, compare=False)
    thumbnail_url: str | None = field(default=None, repr=False, compare=False)
    # Portal local time, explicitly scoped to this loan; no guessed offset.
    due_at: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class LoanSnapshot:
    scope: ProfileScope
    loans: tuple[LoanRecord, ...]
    status: str
    # Validated active-loans widget coverage is scoped to this borrower;
    # never authorizes a return, expiry or deletion by itself.
    exhaustive: bool = False


def _active_loans_may_be_truncated(section: _Node) -> bool:
    """Reject positive completeness if this exact loan widget can load more.

    This is deliberately narrower than the catalogue paginator: search
    recommendations elsewhere on the page must not affect this inventory.
    """
    paging_classes = {
        "pagination", "pager", "load-more", "show-more", "infinite-scroll",
        "spinner", "loading", "skeleton", "js-pagination", "js-load-more",
    }
    # Inspect hidden descendants too: a collapsed paginator or lazy loader
    # may not appear in the public presentation but still truncates coverage.
    pending = [(section, False)]
    while pending:
        node, ancestor_hidden = pending.pop()
        attrs = node.attrs
        hidden = (ancestor_hidden or node.tag in _HIDDEN
                  or "hidden" in attrs or attrs.get("aria-hidden", "").strip().lower() == "true"
                  or "display:none" in "".join(attrs.get("style", "").lower().split())
                  or "visibility:hidden" in "".join(attrs.get("style", "").lower().split()))
        # The visible traversal cannot count hidden loan cards. If a
        # collapsed/template card exists, visible positives remain usable
        # but their count cannot attest exhaustive coverage.
        if hidden and (node is section or node.has_class("js-loan")):
            return True
        pending.extend((child, hidden) for child in node.children
                       if isinstance(child, _Node))
        if attrs.get("aria-busy", "").lower() == "true":
            return True
        if attrs.get("data-has-more", "").lower() in {"true", "1", "yes"}:
            return True
        if attrs.get("data-next-page") or attrs.get("data-next-url"):
            return True
        if paging_classes.intersection(attrs.get("class", "").split()):
            return True
        if "next" in attrs.get("rel", "").split():
            return True
    return False


def parse_loans(html: str, *, page_url: str, expected: ProfileScope,
                observed: ProfileScope) -> LoanSnapshot:
    if not isinstance(expected, ProfileScope) or expected != observed:
        raise WebParseError("scope_mismatch")
    # Caller must obtain observed from its browser session owner, not page text.
    if page_url != ORIGIN + "/my_profile/activity":
        raise WebParseError("invalid_activity_page")
    root = _dom(html)
    sections = [n for n in root.walk() if n.has_class("active-loans")]
    if len(sections) != 1 or not sections[0].closed:
        raise WebParseError("unrecognized_loans")
    cards = [n for n in sections[0].walk() if n.has_class("js-loan")]
    if len(cards) > MAX_ITEMS:
        raise WebParseError("item_limit")
    loans = {}
    partial = False
    quarantined_loan_ids: set[str] = set()
    ambiguous_due_ids: set[str] = set()
    for card in cards:
        if not card.closed:
            partial = True
            continue
        lids, rids = _links(card, "loans"), _links(card, "resources")
        if len(lids) != 1 or len(rids) != 1:
            partial = True
            continue
        titles = {n.text() for n in card.walk() if n.tag == "a"
                  and _id(n.attrs.get("href", ""), "resources") and n.text()}
        author_values = _properties(card).get("author", ())
        # A pathological card cannot flood a Loan model with unrelated text.
        # An excess is uncertainty, not a reason to discard the real loan.
        if len(author_values) > 8:
            partial = True
            author_values = ()
        resource_id = next(iter(rids))
        loan = LoanRecord(next(iter(lids)), resource_id,
                          next(iter(titles)) if len(titles) == 1 else "",
                          tuple(author_values), _loan_cover(card, resource_id),
                          _loan_due_at(card))
        if loan.loan_id in quarantined_loan_ids:
            # The third or later occurrence must not resurrect a loan with
            # two conflicting resource IDs on this same provider page.
            partial = True
            continue
        if loan.loan_id in loans:
            previous = loans[loan.loan_id]
            if previous.resource_id != loan.resource_id:
                # Quarantine *both* meanings of the signed loan identifier.
                # A single broken card must not erase independent positive
                # loans, nor let a first/last-wins item reach token selection.
                loans.pop(loan.loan_id)
                quarantined_loan_ids.add(loan.loan_id)
                partial = True
                continue
            conflicting_cover = previous.thumbnail_url != loan.thumbnail_url
            conflicting_due = previous.due_at != loan.due_at
            if conflicting_due:
                ambiguous_due_ids.add(loan.loan_id)
            if conflicting_cover or conflicting_due or loan.loan_id in ambiguous_due_ids:
                # Metadata contradiction does not erase a validated loan,
                # and later duplicate cards cannot resurrect an uncertain date.
                partial = True
                loan = LoanRecord(previous.loan_id, previous.resource_id,
                                  previous.title, previous.authors,
                                  previous.thumbnail_url if not conflicting_cover else None,
                                  None if loan.loan_id in ambiguous_due_ids else previous.due_at)
            else:
                loan = previous
        loans[loan.loan_id] = loan
    # #4082 owner decision: an authenticated HTTP 200 activity response with
    # exactly one closed active-loans section and no loan cards counts as a
    # complete empty inventory, provided the section is not loading/paginated.
    # The HTTP client already rejects non-200/login/redirect responses. A
    # missing widget, malformed card, hidden card or lazy loader fails closed.
    # This attests *absence* only, never lender acceptance or a loan quota.
    exhaustive = not partial and not _active_loans_may_be_truncated(sections[0])
    return LoanSnapshot(
        expected, tuple(loans.values()),
        "observed" if exhaustive else "partial",
        exhaustive=exhaustive,
    )


def merge_observed_loans(previous: LoanSnapshot, current: LoanSnapshot) -> LoanSnapshot:
    """Non-destructive accumulation, NOT a rights ledger or server sync."""
    if previous.scope != current.scope:
        raise WebParseError("scope_mismatch")
    result = {loan.loan_id: loan for loan in previous.loans}
    for loan in current.loans:
        if loan.loan_id in result and result[loan.loan_id] != loan:
            raise WebParseError("ambiguous_loans")
        result[loan.loan_id] = loan
    if len(result) > MAX_ITEMS:
        raise WebParseError("item_limit")
    return LoanSnapshot(current.scope, tuple(result.values()), "partial")


# #4283: the authenticated reader's history surface is distinct from active
# loans. A history row attests a displayed borrowing event, not its end.
@dataclass(frozen=True)
class HistoryEvent:
    resource_id: str = field(repr=False)
    title: str = field(repr=False)
    borrowed_at: str | None = field(repr=False)


@dataclass(frozen=True)
class HistoryPage:
    scope: ProfileScope = field(repr=False)
    events: tuple[HistoryEvent, ...] = field(repr=False)
    status: str
    displayed_count: int | None
    # Neither server pagination nor exclusion of active loans is qualified.
    exhaustive: bool = False


def _history_direct_nodes(node):
    return [child for child in node.children if isinstance(child, _Node)]


def _history_visible(node):
    return node.tag not in _HIDDEN and "hidden" not in node.attrs and node.attrs.get("aria-hidden") != "true"


def _history_title(node):
    def parts(current):
        if not _history_visible(current) or current.has_class("line-desc-title__suptitle"):
            return
        for child in current.children:
            if isinstance(child, str):
                yield child
            elif isinstance(child, _Node):
                yield from parts(child)
    return " ".join(" ".join(parts(node)).split())[:MAX_TEXT]


def _history_stamp(value):
    if (type(value) is not str or not 20 <= len(value) <= 40
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value)):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None or not 1900 <= parsed.year <= 2100:
            return None
        from datetime import timezone
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def parse_history_events(html: str, *, page_url: str, expected: ProfileScope,
                         observed: ProfileScope) -> HistoryPage:
    """Read only the captured FR history structure, with exact caller scope.

    No HTTP, no pagination guesses, no right or returned-loan status. Even
    an internally reconciled page is not a complete server history. The
    caller must separately establish authenticated session ownership.
    """
    if (type(expected) is not ProfileScope or type(observed) is not ProfileScope
            or expected != observed):
        raise WebParseError("scope_mismatch")
    expected.__post_init__()
    observed.__post_init__()
    if page_url not in (ORIGIN + "/my_profile/history",
                        ORIGIN + "/my_profile/history?l=fr"):
        raise WebParseError("invalid_history_page")
    root = _dom(html)
    bodies = [n for n in root.walk() if n.tag == "body"
              and n.attrs.get("id") == "members" and n.has_class("profile_history")
              and n.attrs.get("data-coffee-cup") == "Members#ProfileHistory"]
    if len(bodies) != 1 or not bodies[0].closed:
        raise WebParseError("unrecognized_history")
    body = bodies[0]
    mains = [n for n in body.walk() if n.attrs.get("id") == "main"]
    if len(mains) != 1 or not mains[0].closed:
        raise WebParseError("unrecognized_history")
    main = mains[0]
    headings = [n for n in main.walk() if n.tag == "h1"
                and n.has_class("list-header__title") and n.closed]
    nav = [n for n in main.walk() if n.tag == "a" and n.has_class("aside-nav__current")
           and n.attrs.get("href") in (ORIGIN + "/my_profile/history", "/my_profile/history",
                                       ORIGIN + "/my_profile/history?l=fr", "/my_profile/history?l=fr")]
    account_menu = [n for n in body.walk() if n.tag == "a"
                    and n.has_class("nav-account__link-account")]
    if len(headings) != 1 or headings[0].text() != "Historique" or len(nav) != 1 or not account_menu:
        raise WebParseError("unrecognized_history")

    counters = [n for n in main.walk() if n.has_class("list-header__page") and n.closed]
    values = [n.text() for counter in counters for n in counter.walk()
              if n.tag == "strong" and n.closed]
    count = int(values[0]) if (len(counters) == 1 and len(values) == 1
                               and re.fullmatch(r"[0-9]{1,7}", values[0])) else None
    lists = [n for n in main.walk() if n.tag == "ul" and n.has_class("list-ui")]
    if len(lists) != 1 or not lists[0].closed:
        raise WebParseError("unrecognized_history")
    children = _history_direct_nodes(lists[0])
    if len(children) > MAX_ITEMS:
        raise WebParseError("item_limit")
    partial = count is None
    events = {}
    uncertain = []
    conflicts = set()
    card_count = 0
    for card in children:
        if not _history_visible(card):
            partial = True
            continue
        if card.tag != "li" or not card.has_class("input-group") or not card.closed:
            partial = True
            continue
        card_count += 1
        links = [n for n in card.walk() if n.tag == "a"
                 and _id(n.attrs.get("href", ""), "resources") is not None]
        if len(links) != 1 or not links[0].closed:
            partial = True
            continue
        link = links[0]
        rid = _id(link.attrs.get("href", ""), "resources")
        if not re.fullmatch(r"[a-fA-F0-9]{24}", rid):
            partial = True
            continue
        # The captured href is an edition coordinate, not a loan identifier.
        titles = [n for n in link.walk() if n.tag == "h2"
                  and n.has_class("line-desc-title") and n.closed]
        types = [n for title in titles for n in title.walk()
                 if n.has_class("line-desc-title__suptitle") and n.closed]
        if len(titles) != 1 or len(types) != 1 or types[0].text() != "Prêt":
            partial = True
            continue
        title = _history_title(titles[0])
        if not title:
            partial = True
            continue
        info = [n for n in link.walk() if n.has_class("line-desc-supinfo") and n.closed]
        times = [n for row in info for n in row.walk() if n.tag == "time" and n.closed]
        stamp = (_history_stamp(times[0].attrs.get("datetime", ""))
                 if len(info) == 1 and info[0].text().startswith("Emprunté le ")
                 and len(times) == 1 else None)
        event = HistoryEvent(rid, title, stamp)
        if stamp is None:
            # No stable event coordinate: retain descriptive row, no ID guess.
            uncertain.append(event)
            partial = True
            continue
        key = (rid, stamp)
        if key in conflicts:
            partial = True
            continue
        if key in events:
            partial = True  # indistinguishable events cannot prove a count
            if events[key] != event:
                del events[key]
                conflicts.add(key)
        else:
            events[key] = event
    partial |= count != card_count or not card_count or len(events) != card_count
    # Pagination is not qualified. Links or hidden/truncated rows are never
    # followed here. A zero fixture stays partial, not a proved empty history.
    if _active_loans_may_be_truncated(main):
        partial = True
    return HistoryPage(expected, tuple(events.values()) + tuple(uncertain),
                       "partial" if partial else "observed", count)
