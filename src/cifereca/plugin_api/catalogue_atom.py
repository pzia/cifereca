"""#6590 structural Atom feed, without transport or catalogue semantics.

One bounded XML parse yields the same entry elements and direct feed links.
Provider-specific projections may read additional namespaces and child
markup from these elements. Do not mutate the returned tree or interpret
an acquisition link as a capability/right.
"""
from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .catalogue_xml import parse_bounded_catalogue_xml, CatalogueXMLParseError

_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True, slots=True, repr=False)
class AtomLink:
    href: str
    rel: str
    media_type: str
    title: str


@dataclass(frozen=True, slots=True, repr=False)
class AtomCatalogueFeed:
    entries: tuple[ET.Element, ...]
    links: tuple[AtomLink, ...]


def parse_atom_catalogue(content: bytes) -> AtomCatalogueFeed:
    """Parse one actual Atom feed; unrelated XML and HTML are not zero hits.

    Direct feed-level links are distinct from entry-level links, which
    remain in the original elements. Preserve raw attributes, including
    empty href and absent rel; defaulting, normalization, URL resolution,
    navigation/publication classification and filtering are adapter policy.
    """
    root = parse_bounded_catalogue_xml(content)
    if root.tag != _ATOM + "feed":
        raise CatalogueXMLParseError("unexpected_root")
    links = tuple(AtomLink(
        href=node.get("href", ""), rel=node.get("rel", ""),
        media_type=node.get("type", ""), title=node.get("title", ""),
    ) for node in root.findall(_ATOM + "link"))
    return AtomCatalogueFeed(tuple(root.findall(_ATOM + "entry")), links)
