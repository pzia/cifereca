"""Bounded, transport-free XML parsing for standard supplier catalogues.

No network, policy, storage, session, rights or catalogue interpretation.
The caller owns HTTP byte/decompression limits, root/namespace validation,
and URL acceptance. XML parsing happens exactly once per invocation.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_XML_ELEMENTS = 12000
MAX_XML_DEPTH = 96


class CatalogueXMLParseError(ValueError):
    """Closed public reason: malformed, html, forbidden_doctype or budget."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _BoundedTree(ET.TreeBuilder):
    def __init__(self, depth, elements):
        super().__init__()
        self.depth = 0
        self.count = 0
        self.max_depth = depth
        self.max_elements = elements

    def doctype(self, name, pubid, system):
        # Parser-level rejection also catches BOM and UTF-16 declarations;
        # scanning ASCII bytes for <!DOCTYPE cannot establish this safety.
        raise CatalogueXMLParseError("forbidden_doctype")

    def start(self, tag, attrs):
        self.depth += 1
        self.count += 1
        if self.depth > self.max_depth or self.count > self.max_elements:
            raise CatalogueXMLParseError("budget")
        return super().start(tag, attrs)

    def end(self, tag):
        value = super().end(tag)
        self.depth -= 1
        return value


def _html_prefix(content):
    # Classification only. Never use a decoded prefix to approve XML.
    # Recognize common proxy/login pages including UTF-16 and UTF-8 BOM.
    prefix = content[:512]
    if prefix.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        text = prefix.decode("utf-32", errors="ignore")
    elif prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = prefix.decode("utf-16", errors="ignore")
    elif prefix.startswith(b"\x00<"):
        text = prefix.decode("utf-16-be", errors="ignore")
    elif prefix.startswith(b"<\x00"):
        text = prefix.decode("utf-16-le", errors="ignore")
    else:
        text = prefix.decode("utf-8-sig", errors="ignore")
    text = text.lstrip(" \t\r\n\ufeff").casefold()
    return text.startswith(("<html", "<!doctype html"))


def parse_bounded_catalogue_xml(
    content: bytes, *, max_bytes=MAX_XML_BYTES,
    max_depth=MAX_XML_DEPTH, max_elements=MAX_XML_ELEMENTS,
) -> ET.Element:
    """Return the one parsed root; never interpret a feed or fetch links.

    Lower per-caller budgets are supported; raising global safety limits
    requires a deliberate contract change. An HTML root (including XHTML)
    or obvious malformed HTML proxy response is not an empty XML feed.
    Exceptions contain only a closed reason and no source text or parser
    context that might disclose payloads.
    """
    for value, ceiling in ((max_bytes, MAX_XML_BYTES),
                           (max_depth, MAX_XML_DEPTH),
                           (max_elements, MAX_XML_ELEMENTS)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError("invalid_catalogue_xml_budget")
    if type(content) is not bytes or not 1 <= len(content) <= max_bytes:
        raise CatalogueXMLParseError("budget")
    failure = None
    try:
        root = ET.fromstring(content, parser=ET.XMLParser(
            target=_BoundedTree(max_depth, max_elements),
        ))
    except CatalogueXMLParseError as exc:
        failure = exc.code
    except ET.ParseError:
        failure = "html" if _html_prefix(content) else "malformed"
    except (ValueError, LookupError, RecursionError, OverflowError):
        failure = "malformed"
    if failure is not None:
        # Outside catch: never retain a parser exception in __context__.
        raise CatalogueXMLParseError(failure)
    if type(root.tag) is str and root.tag.rsplit("}", 1)[-1].casefold() == "html":
        raise CatalogueXMLParseError("html")
    return root
