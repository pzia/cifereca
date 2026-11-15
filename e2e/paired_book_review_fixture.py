"""P1 #774: ONE deterministic, public, offline book for the two real GTK views.

This fixture supplies only bibliography. It is NOT an entitlement,
provider credential, real provenance claim or a user catalogue record.
"""
from __future__ import annotations

import hashlib
import json

FIXTURE_ID = "p1-774-paired-public-book-gallica-v1"
BOOK = {
    "title": "Camille et les livres 003",
    "author": "Autrice E2E",
    "source_provider": "gallica",
    "record_id": "camille-003",
    "isbn": "9780306406157",
    "subject": "Astronomie",
}


def document_sha256() -> str:
    encoded = json.dumps(
        BOOK, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verified_fixture_receipt() -> dict[str, object]:
    """Only probes may emit this after asserting their actual GTK row."""
    return {
        "fixture_id": FIXTURE_ID,
        "document_sha256": document_sha256(),
        "source_provider": BOOK["source_provider"],
        "record_id": BOOK["record_id"],
        "verified_by_gtk": True,
    }
