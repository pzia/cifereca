"""P1 #4496: readable PDF filenames require unique embedded XMP evidence.

The exact source receipt hash remains in every final path; no provider title,
network request or migration of opaque legacy copies is introduced.
"""
from io import BytesIO
from pathlib import Path

from cifereca.core.canonical_promotion import _readable_pdf_target_names
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_selected_candidate_finalization import (
    rig, _prepare, _isolate_delivery_lock,
)


XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/"
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <rdf:RDF><rdf:Description>
 <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
 <dc:creator><rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq></dc:creator>
 </rdf:Description></rdf:RDF></x:xmpmeta>"""


def pdf(*, title="Titre PDF vérifié", author="Auteure Test", marker=True):
    packet = XMP.format(title=title, author=author).encode("utf-8")
    metadata = b"/Type /Metadata /Subtype /XML " if marker else b""
    return (
        b"%PDF-1.7\n1 0 obj\n<<" + metadata + b">>\nstream\n"
        + packet + b"\nendstream\nendobj\n%%EOF\n"
    )


def test_attested_pdf_packet_gives_stable_human_name_with_full_receipt_hash():
    digest = "a" * 64
    original = pdf()
    names = _readable_pdf_target_names(BytesIO(original), opaque_name=digest)
    assert names[0] == digest + ".pdf"
    assert names[-1].startswith("Auteure Test - Titre PDF vérifié - ")
    assert names[-1].endswith(" - " + digest + ".pdf")
    assert names == _readable_pdf_target_names(BytesIO(original), opaque_name=digest)
    assert len(names[-1].encode("utf-8")) <= 196


def test_pdf_without_unambiguous_attested_xmp_retains_opaque_filename():
    digest = "f" * 64
    for payload in (
        pdf(marker=False),
        pdf().replace(b"</x:xmpmeta>", b""),
        pdf().replace(b"</x:xmpmeta>", b"</x:xmpmeta> <x:xmpmeta/>"),
        pdf(title=""),
        pdf(author=""),
        pdf(title="evil/../\\path"),
        pdf(title="https://private.example/key"),
        b"%PDF-1.7\n%no metadata\n%%EOF",
    ):
        names = _readable_pdf_target_names(BytesIO(payload), opaque_name=digest)
        # A slash/path in metadata must never escape the destination.
        if payload == pdf(title="evil/../\\path"):
            assert names[-1].endswith(digest + ".pdf")
            assert "/" not in names[-1] and "\\" not in names[-1]
        else:
            assert names == (digest + ".pdf",)


def test_canonical_pdf_sync_uses_readable_acquisitions_and_no_repeat_get(rig):
    service, db, root = rig
    _prepare(service)
    payload = pdf()
    gets = []

    def candidate(_account, *, target_dir, **_kwargs):
        gets.append("GET")
        file = Path(target_dir) / "candidate.pdf"
        file.write_bytes(payload)
        return file, "synthetic-provider-pdf"

    service.session.rights_selected_delivery_candidate = candidate
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1, first
    assert gets == ["GET"]
    placement, = db.placements()
    final = Path(placement["path"])
    assert final.parent == root / "A" / "Acquisitions"
    assert final.name.startswith("Auteure Test - Titre PDF vérifié - ")
    assert final.name.endswith(".pdf")
    assert len(final.stem.rsplit(" - ", 1)[-1]) == 64
    assert final.read_bytes() == payload
    replay = synchronize_account_source(service, service.account)
    assert replay["canonical_execution"]["present_attested"] == 1
    assert gets == ["GET"]
    assert Path(db.placements()[0]["path"]) == final
