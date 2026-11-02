"""Versioned, additive documentary classification independent of supplier rights.

A manifestation is not a work, a delivery offer, an Artifact or a licence.
Unknown and conflicting evidence must survive projection; legacy SearchResult
fields are NOT attestation (e.g. Paris formerly guessed physical from CATALOGUE).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from cifereca.models import SearchResult


DOCUMENT_CLASSIFICATION_CONTRACT = "cifereca.document.classification/1"

EvidenceStatus = Literal["attested", "inferred_bounded", "unknown", "conflict"]
DocumentMedium = Literal["digital", "physical", "unknown"]
DocumentKind = Literal[
    "book", "comics", "audio", "audiobook", "music", "video",
    "periodical", "other", "unknown",
]
MetaType = Literal[
    "EBOOK", "AUDIOBOOK", "PRINTED_BOOK", "COMIC",
    "MUSIC", "VIDEO", "PERIODICAL", "UNKNOWN",
]
_STATUSES = {"attested", "inferred_bounded", "unknown", "conflict"}
_MEDIA = {"digital", "physical", "unknown"}
_KINDS = {
    "book", "comics", "audio", "audiobook", "music", "video",
    "periodical", "other", "unknown",
}
# These are manifestation formats; Syracuse Resource.Frmt is *not* one.
_MATERIAL_FORMATS = {"printed_book", "comics_album", "cd", "dvd", "blu_ray", "other", "unknown"}
_FILE_FORMATS = {"epub", "kepub", "pdf", "mp3", "m4a", "other", "unknown"}


@dataclass(frozen=True, slots=True)
class ClassificationEvidence:
    """One bounded fact, with provenance and non-logging raw upstream code.

    A missing fact is explicitly unknown; conflicts are explicit and cannot
    carry a preferred winner. `raw_code` is not safe for logs or diagnostics.
    """

    value: str = "unknown"
    status: EvidenceStatus = "unknown"
    source_key: str | None = None
    raw_code: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError("invalid classification evidence status")
        if self.status in {"unknown", "conflict"} and self.value != "unknown":
            raise ValueError("an unknown/conflicting fact cannot choose a value")
        if self.status in {"attested", "inferred_bounded"} and self.value == "unknown":
            raise ValueError("a positive fact needs a known value")
        if self.source_key is not None and not isinstance(self.source_key, str):
            raise TypeError("source_key must be a string")


@dataclass(frozen=True, slots=True)
class DocumentClassification:
    """DocumentClassification/1: projection of one exact manifestation.

    Physical presentation, digital file formats offered and received bytes are
    different notions. No attribute in this DTO authorizes borrowing, download,
    ownership, entitlement or consolidation of exact source references.
    """

    medium: ClassificationEvidence = field(default_factory=ClassificationEvidence)
    document_kind: ClassificationEvidence = field(default_factory=ClassificationEvidence)
    material_format: ClassificationEvidence = field(default_factory=ClassificationEvidence)
    digital_format: ClassificationEvidence = field(default_factory=ClassificationEvidence)
    offered_file_formats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, allowed in (
            ("medium", _MEDIA), ("document_kind", _KINDS),
            ("material_format", _MATERIAL_FORMATS),
            ("digital_format", _FILE_FORMATS),
        ):
            fact = getattr(self, name)
            if not isinstance(fact, ClassificationEvidence) or fact.value not in allowed:
                raise ValueError(f"invalid {name} evidence")
        if self.material_format.status in {"attested", "inferred_bounded"}:
            if self.medium.value != "physical" or self.medium.status in {"unknown", "conflict"}:
                raise ValueError("physical presentation requires positive physical support")
        if self.digital_format.status in {"attested", "inferred_bounded"}:
            if self.medium.value != "digital" or self.medium.status in {"unknown", "conflict"}:
                raise ValueError("digital format requires positive digital support")
        if type(self.offered_file_formats) is not tuple or any(
            type(value) is not str or value not in _FILE_FORMATS - {"unknown"}
            for value in self.offered_file_formats
        ):
            raise ValueError("offered formats must be a tuple of known format codes")
        if len(set(self.offered_file_formats)) != len(self.offered_file_formats):
            raise ValueError("duplicate offered file format")

    @property
    def meta_type(self) -> MetaType:
        """Conservative editorial label; never an authorization or default.

        EBOOK requires positively attested digital *book* evidence, not an EBOOKS
        search route, ISBN, title similarity, a claimed extension or a right.
        """
        medium, kind = self.medium, self.document_kind
        if medium.status != "attested" or kind.status != "attested":
            return "UNKNOWN"
        if kind.value == "book":
            if medium.value == "digital":
                return "EBOOK"
            if medium.value == "physical":
                return "PRINTED_BOOK"
        return {
            "comics": "COMIC", "audiobook": "AUDIOBOOK", "music": "MUSIC",
            "video": "VIDEO", "periodical": "PERIODICAL",
        }.get(kind.value, "UNKNOWN")

    def matches_filter(self, requested: str) -> bool:
        """UI selection on positive evidence only; 'all' keeps unknowns."""
        if requested == "all":
            return True
        if requested in {"digital", "physical"}:
            return (self.medium.status == "attested"
                    and self.medium.value == requested)
        if requested == "ebook":
            return self.meta_type == "EBOOK"
        if requested == "printed_book":
            return self.meta_type == "PRINTED_BOOK"
        return False


def classification_for_search_result(row: SearchResult) -> DocumentClassification:
    """Additive bridge: legacy fields remain unchanged but imply no new facts.

    In particular SearchResult.medium='physical' can be a historic guess made
    solely from the CATALOGUE route, and `format='MONE'` is not a file codec.
    Only an explicit, already validated provider classification is authoritative.
    """
    fact = getattr(row, "document_classification", None)
    return fact if isinstance(fact, DocumentClassification) else DocumentClassification()
