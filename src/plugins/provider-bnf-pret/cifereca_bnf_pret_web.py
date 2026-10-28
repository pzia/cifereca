"""Bounded, offline projections of the reader portal DOM (#2055).

No HTTP, browser state, SAML, mutation or acquisition. Outputs are private
application data, never diagnostic payloads. Synthetic selectors are documented
in WEB.md; a successful parse does not authenticate a session or attest a right.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
        if self.tag in _HIDDEN or "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true":
            return
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def text(self):
        if self.tag in _HIDDEN or "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true":
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
                               single('description'))
        if rid in records and records[rid] != record:
            partial = True
        else:
            records[rid] = record
    return CatalogPage(tuple(records.values()), "partial" if partial else "ok")


def _properties(root):
    result = {}
    allowed = {"name", "author", "publisher", "isbn", "inLanguage", "description"}
    def nodes(node):
        if node.tag in _HIDDEN or "hidden" in node.attrs or node.attrs.get("aria-hidden") == "true":
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
    unavailable = any(n.has_class("lb_unavailable") and n.text() for n in books[0].walk())
    record = CatalogRecord(rid, props["name"][0], tuple(props.get("author", ())),
                           single("publisher"), single("isbn"), single("inLanguage"),
                           single("description"), "unavailable" if unavailable else "unknown")
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
    """A query-free public image URL, never a private loan or SSO endpoint.

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


def _loan_cover(card: _Node, resource_id: str) -> str | None:
    """Only the exact resource link in this loan card can attest an image.

    A global image, profile avatar, homonymous catalogue image, or second
    conflicting cover stays unknown. Never use a title/ISBN fallback.
    """
    sources = []
    for anchor in card.walk():
        if anchor.tag != 'a' or _id(anchor.attrs.get('href', ''), 'resources') != resource_id:
            continue
        for node in anchor.walk():
            if node.tag == 'img':
                sources.append(node.attrs.get('data-src') or node.attrs.get('src') or '')
    if not sources or len(sources) > 3:
        return None
    images = [_public_cover_url(value) for value in sources]
    if any(value is None for value in images) or len(set(images)) != 1:
        return None
    return images[0]


@dataclass(frozen=True)
class LoanRecord:
    loan_id: str = field(repr=False)
    resource_id: str = field(repr=False)
    title: str = field(default="", repr=False, compare=False)
    # Only Book/loan-card microdata can attest a public bibliographic author.
    # Do not use headers, linked catalogue names or unrelated book scopes.
    authors: tuple[str, ...] = field(default=(), repr=False, compare=False)
    thumbnail_url: str | None = field(default=None, repr=False, compare=False)
    # Raw dates intentionally absent: no date selector has been qualified.


@dataclass(frozen=True)
class LoanSnapshot:
    scope: ProfileScope
    loans: tuple[LoanRecord, ...]
    status: str
    # A single page is never evidence of complete server inventory/deletions.
    exhaustive: bool = False


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
                          tuple(author_values), _loan_cover(card, resource_id))
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
            if previous.thumbnail_url != loan.thumbnail_url:
                # Duplicate cards with conflicting evidence must not choose
                # a cover arbitrarily; keep the positive loan but no image.
                partial = True
                loan = LoanRecord(previous.loan_id, previous.resource_id,
                                  previous.title, previous.authors)
            else:
                loan = previous
        loans[loan.loan_id] = loan
    # Empty section may mean pagination, incomplete HTML or loading state.
    return LoanSnapshot(expected, tuple(loans.values()), "partial" if partial or not loans else "observed")


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
