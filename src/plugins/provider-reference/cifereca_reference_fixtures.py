"""Deterministic, immutable data for the reference provider (#3722).

This is a paid-provider *simulator*, not a real authenticated service. No
credentials, network endpoints, book titles or file paths from users are read.
The real Core owns acquisition authorization, consent and all persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import zipfile


@dataclass(frozen=True, slots=True)
class ReferenceResource:
    edition_id: str
    kind: str = "purchase"
    state: str = "unknown"
    delivery: str = "available"
    archived: bool = False
    payload_id: str = ""
    interrupt_once: bool = False


_MIXED = (
    ReferenceResource("epub-001", state="active"),
    ReferenceResource("epub-002"),  # available without an active entitlement
    ReferenceResource("loan-003", kind="loan"),
    ReferenceResource("no-offer-004", state="active", delivery="unknown"),
    ReferenceResource("refused-005", state="active", delivery="unavailable"),
    ReferenceResource("archived-006", state="active", delivery="unavailable",
                      archived=True),
    ReferenceResource("same-sha-007", payload_id="shared-book"),
    ReferenceResource("same-sha-008", payload_id="shared-book"),
    ReferenceResource("interrupted-009", interrupt_once=True),
)


def resources(scenario: str, *, bulk_count: int = 350) -> tuple[ReferenceResource, ...]:
    if scenario == "mixed":
        return _MIXED
    if scenario == "empty":
        return ()
    if scenario == "bulk" and type(bulk_count) is int and 1 <= bulk_count <= 500:
        return tuple(ReferenceResource(f"bulk-{i:04d}") for i in range(bulk_count))
    raise ValueError("reference_invalid_scenario")


def epub_bytes(resource: ReferenceResource) -> bytes:
    """Build a reproducible, structurally valid EPUB; same payload_id = same SHA.

    Fixed ZIP timestamps and insertion order permit byte-exact repetitions
    across accounts/runs. Provider never grants permissions based on this SHA.
    """
    identity = resource.payload_id or resource.edition_id
    if not identity.isascii() or not identity.replace("-", "").replace("_", "").isalnum():
        raise ValueError("reference_invalid_payload")
    body = f"<html xmlns='http://www.w3.org/1999/xhtml'><body><p>Fictif {identity}</p></body></html>"
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>Fictif {identity}</dc:title><dc:identifier id="bookid">'
        f'urn:cifereca:reference:{identity}</dc:identifier>'
        '<dc:language>fr</dc:language></metadata><manifest>'
        '<item id="body" href="text.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="body"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    files = (
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", container.encode("utf-8")),
        ("OEBPS/content.opf", opf.encode("utf-8")),
        ("OEBPS/text.xhtml", body.encode("utf-8")),
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, content)
    return output.getvalue()
