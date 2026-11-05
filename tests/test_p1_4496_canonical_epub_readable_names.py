"""P1 #4496: human-readable new canonical EPUB acquisitions, never a right.

The canonical receipt digest remains the full discriminator. Existing opaque
paths and PDFs are retained, no migration, no provider HTTP, no overwrite.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_STORED

import pytest

from cifereca.core import canonical_promotion, convergence_execution
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_selected_candidate_finalization import (
    rig, _isolate_delivery_lock, _prepare,
)


def _epub(title="Livre d'essai", author="Émile Exemple", extra_metadata=""):
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", (
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' version="1.0"><rootfiles><rootfile'
            ' full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>'
        ))
        archive.writestr("OPS/book.opf", (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>'
            f'{extra_metadata}'
            '</metadata><manifest><item id="chapter" href="chapter.xhtml"'
            ' media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="chapter"/></spine></package>'
        ))
        archive.writestr("OPS/chapter.xhtml", (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<p>Local synthetic text</p></body></html>'
        ))
    return payload.getvalue()


def _readable_epub_target_name(stream, *, opaque_name):
    return canonical_promotion._readable_epub_target_names(stream, opaque_name=opaque_name)[-1]


_EPUB3_SERIES = (
    '<meta property="belongs-to-collection" id="cycle">Cycle</meta>'
    '<meta refines="#cycle" property="collection-type">series</meta>'
    '<meta refines="#cycle" property="group-position">2</meta>'
)
_CALIBRE_SERIES = (
    '<meta name="calibre:series" content="Cycle"/>'
    '<meta name="calibre:series_index" content="2"/>'
)


@pytest.mark.parametrize("embedded", [_EPUB3_SERIES, _CALIBRE_SERIES, _EPUB3_SERIES + _CALIBRE_SERIES])
def test_series_and_volume_are_taken_from_the_same_embedded_declaration(embedded):
    digest = "b" * 64
    legacy, preferred = canonical_promotion._readable_epub_target_names(
        BytesIO(_epub(extra_metadata=embedded)), opaque_name=digest,
    )
    assert legacy == f"Émile Exemple - Livre d'essai - {digest}.epub"
    assert preferred == f"Émile Exemple - Cycle - 2 - Livre d'essai - {digest}.epub"


@pytest.mark.parametrize("embedded", [
    _EPUB3_SERIES.replace('refines="#cycle" property="group-position"',
                          'refines="#other" property="group-position"'),
    _EPUB3_SERIES.replace('>series</meta>', '>set</meta>'),
    _EPUB3_SERIES.replace('>2</meta>', '>../2</meta>'),
    _EPUB3_SERIES + '<meta refines="#cycle" property="group-position">3</meta>',
    _EPUB3_SERIES + _CALIBRE_SERIES.replace('content="2"', 'content="3"'),
    _EPUB3_SERIES.replace('>2</meta>', '></meta>') + _CALIBRE_SERIES,
])
def test_unbound_or_ambiguous_series_keeps_existing_readable_name(embedded):
    digest = "c" * 64
    names = canonical_promotion._readable_epub_target_names(
        BytesIO(_epub(extra_metadata=embedded)), opaque_name=digest,
    )
    assert names == (f"Émile Exemple - Livre d'essai - {digest}.epub",)


def test_long_series_and_author_keep_title_and_full_discriminator():
    digest = "a" * 64
    embedded = _EPUB3_SERIES.replace('>Cycle</meta>', '>' + 'É' * 120 + '/Saga</meta>')
    name = _readable_epub_target_name(
        BytesIO(_epub(author="Å" * 120, extra_metadata=embedded)), opaque_name=digest,
    )
    assert name.endswith(f" - 2 - Livre d'essai - {digest}.epub")
    assert len(name.encode("utf-8")) <= 192
    assert "/" not in name


def test_verified_epub_metadata_yields_stable_readable_receipt_name():
    digest = "f" * 64
    content = _epub()
    one = _readable_epub_target_name(BytesIO(content), opaque_name=digest)
    two = _readable_epub_target_name(BytesIO(content), opaque_name=digest)
    assert one == two
    assert one.startswith("Émile Exemple - Livre d'essai - ")
    assert one.endswith(digest + ".epub")
    assert "/" not in one and "\\" not in one
    assert _readable_epub_target_name(
        BytesIO(b"not an epub"), opaque_name=digest,
    ) == digest + ".epub"


def test_untrusted_epub_title_never_becomes_path_or_hides_receipt():
    # XML-safe control/path tokens are sanitized by the shared filename rule.
    digest = "e" * 64
    title = "Un / chemin\\.. interdit"
    name = _readable_epub_target_name(
        BytesIO(_epub(title=title, author="Auteur")), opaque_name=digest,
    )
    assert name.startswith("Auteur - Un chemin .. interdit - ")
    assert name.endswith(digest + ".epub")
    assert "/" not in name and "\\" not in name
    long_title = "É" * 800
    name = _readable_epub_target_name(
        BytesIO(_epub(title=long_title)), opaque_name=digest,
    )
    assert len(name.encode("utf-8")) <= 196
    assert name.endswith(digest + ".epub")


def test_canonical_sync_publishes_human_epub_once_without_fresh_get(rig):
    service, db, root = rig
    _prepare(service)
    contents = _epub(extra_metadata=_EPUB3_SERIES)
    requests = []

    def candidate(_account, *, target_dir, **_kwargs):
        requests.append("GET")
        received = Path(target_dir) / "received.epub"
        received.write_bytes(contents)
        return received, "synthetic-provider-epub"

    service.session.rights_selected_delivery_candidate = candidate
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert requests == ["GET"]
    placements = db.placements()
    assert len(placements) == 1
    path = Path(placements[0]["path"])
    assert path.parent == root / "A" / "Acquisitions"
    assert path.name.startswith("Émile Exemple - Cycle - 2 - Livre d'essai - ")
    assert path.name.endswith(".epub")
    assert len(path.stem.split(" - ")[-1]) == 64
    assert path.read_bytes() == contents
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert requests == ["GET"]
    assert len(db.placements()) == 1
    assert Path(db.placements()[0]["path"]) == path


def test_recovery_reuses_pre_upgrade_readable_copy(rig, monkeypatch):
    service, db, root = rig
    _prepare(service)
    contents = _epub(extra_metadata=_EPUB3_SERIES)
    requests = []

    def candidate(_account, *, target_dir, **_kwargs):
        requests.append("GET")
        received = Path(target_dir) / "received.epub"
        received.write_bytes(contents)
        return received, "synthetic-provider-epub"

    def interrupted(**_kwargs):
        raise RuntimeError("synthetic interruption before SQLite commit")

    service.session.rights_selected_delivery_candidate = candidate
    target_names = canonical_promotion._readable_epub_target_names
    with monkeypatch.context() as patch:
        patch.setattr(canonical_promotion, "_readable_epub_target_names",
                      lambda *args, **kwargs: target_names(*args, **kwargs)[:1])
        patch.setattr(convergence_execution, "publish_completed_acquisition", interrupted)
        first = synchronize_account_source(service, service.account)

    assert first["canonical_execution"]["failed"] == 1
    assert not db.placements()
    directory = root / "A" / "Acquisitions"
    old_path, = directory.iterdir()
    assert old_path.name.startswith("Émile Exemple - Livre d'essai - ")
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["recovered"] == 1
    assert Path(db.placements()[0]["path"]) == old_path
    assert list(directory.iterdir()) == [old_path]
    assert requests == ["GET"]
