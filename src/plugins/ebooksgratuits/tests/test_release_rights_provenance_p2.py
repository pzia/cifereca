"""P2 #764: EbooksGratuits edition release and territorial copyright warnings.

The official public list and detail pages explicitly show "Parution le
31/08/2026". The public Le Hussard bleu listing explicitly warns that
download is forbidden in some countries. HTML below is SYNTHETIC,
not a captured upstream DOM fixture.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca_ebooksgratuits import (
    EbooksSession, Parser, _ebook_release_evidence, _rights_warning_attested,
)


def card(book_id, title, *, release="", warning="", body="Description"):
    return f"""<li class="ebook">
      <a href="details.php?book={book_id}"><span class="listeb">{title}</span></a>
      <span class="auteur">Auteur {book_id}</span>
      <p class="resume">{body}</p>
      <p>Édition Ebooks libres et gratuits. - {release}</p>
      <div><span>{warning}</span></div>
      <a href="newsendbook.php?format=epub&amp;id={book_id}">ePub : 165 Ko</a>
    </li>"""


def fixture():
    return "<ul>" + (
        card("3500", "Nouveaux contes du Far West",
             release="Parution le 31/08/2026")
        + card("3498", "Le Hussard bleu", release="Parution le 31/08/2026",
               warning="Attention: téléchargement non autorisé dans certains pays. "
                       "Lire la note sur le droit d’auteur. Auteur décédé en 1962.")
        + card("50", "Œuvre sans date", body="La première édition date de 1850")
    ) + "</ul>"


class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def catalog_session(document):
    session = EbooksSession(
        credential_id="public", label="", credentials={}, options={"acquisition_backend":"html"},
        config={}, diagnostics=None, transport=None,
    )
    session.connect()
    session.client.close()
    session.client = SimpleNamespace(get=lambda _url: Response(document), close=lambda: None)
    return session


def test_catalog_attests_only_ebook_edition_release_not_original_print_year():
    rows = Parser().rows(fixture())
    assert len(rows) == 3
    assert rows[0]["ebook_release_date"] == "2026-08-31"
    assert rows[1]["ebook_release_date"] == "2026-08-31"
    assert rows[2]["ebook_release_date"] is None
    session = catalog_session(fixture())
    try:
        titles = session.search("")
        assert len(titles) == 3
        release = titles[0]
        assert release.published_at is None
        assert release.metadata["ebook_release_date"] == "2026-08-31"
        assert release.metadata["ebook_release_evidence"] == "ebooksgratuits.catalogue.parution"
        assert release.metadata["license_note"] == "Vérifier les droits selon le pays"
        assert "territorial_rights_warning" not in release.metadata
        assert titles[2].published_at is None
        assert "ebook_release_date" not in titles[2].metadata
        assert titles[2].description == "La première édition date de 1850"
    finally:
        session.close()


def test_public_territorial_warning_is_distinct_per_book_and_not_hidden():
    session = catalog_session(fixture())
    try:
        first, restricted, third = session.search("")
        assert "territorial_rights_warning" not in first.metadata
        assert restricted.metadata["territorial_rights_warning"] is True
        assert restricted.metadata["territorial_rights_evidence"] == (
            "ebooksgratuits.catalogue.warning"
        )
        assert "non autorisé dans certains pays" in restricted.metadata["license_note"]
        assert restricted.downloadable is True  # user decides legal territory
        assert restricted.download_url.endswith("id=3498")
        assert "territorial_rights_warning" not in third.metadata
        assert "1962" not in repr(restricted.metadata)
    finally:
        session.close()


@pytest.mark.parametrize("label,expected", [
    ("Édition ELG - Parution le 31/08/2026", "2026-08-31"),
    ("Parution\nle\t01/01/2000", "2000-01-01"),
    ("Réédition en 1865 (œuvre originale)", None),
    ("Auteur décédé en 1962", None),
    ("Parution le 31/02/2026", None),
    ("Parution le 99/08/2026", None),
    ("Parution le 31/13/2026", None),
    ("Parution le 2026-08-31", None),
    ("Parution du livre en 1980", None),
    ("", None),
])
def test_release_date_requires_explicit_parution_label_and_valid_calendar(label, expected):
    assert _ebook_release_evidence(label) == expected


def test_warning_requires_exact_public_territorial_notice():
    assert _rights_warning_attested(
        "Attention: téléchargement non autorisé dans certains pays."
    )
    assert not _rights_warning_attested("Licence libre — téléchargement autorisé")
    assert not _rights_warning_attested("Auteur décédé en 1962")
    assert not _rights_warning_attested("Téléchargement autorisé dans tous les pays")


def test_fragmented_html_and_comments_do_not_leak_raw_text_in_metadata():
    document = (
        "<ul>" + card("3500", "Contes",
                       release="<span>Parution</span><span>le</span><b>31/08/2026</b>",
                       warning="<span>Attention: téléchargement</span> "
                               "<strong>non autorisé dans certains pays.</strong>",
                       body="<script>auth_token=SECRET</script>Texte public") + "</ul>"
    )
    session = catalog_session(document)
    try:
        item = session.search("")[0]
        assert item.metadata["ebook_release_date"] == "2026-08-31"
        assert item.metadata["territorial_rights_warning"] is True
        assert "SECRET" not in repr(item.metadata)
        assert "auth_token" not in repr(item.metadata)
        assert "1962" not in repr(item.metadata)
    finally:
        session.close()



def test_non_visible_scripts_templates_cannot_fabricate_public_release_or_warning():
    hidden = (
        "<script>Parution le 01/01/1990. "
        "Attention: téléchargement non autorisé dans certains pays.</script>"
        "<style>.hidden:after { content: 'Parution le 01/01/1980'; }</style>"
        "<template>Parution le 02/02/1970. "
        "Attention: téléchargement non autorisé dans certains pays.</template>"
    )
    session = catalog_session("<ul>" + card(
        "50", "Livre sans date réelle", release=hidden,
    ) + "</ul>")
    try:
        item = session.search("")[0]
        assert item.published_at is None
        assert "ebook_release_date" not in item.metadata
        assert "territorial_rights_warning" not in item.metadata
        assert item.metadata["license_note"] == "Vérifier les droits selon le pays"
    finally:
        session.close()


def test_source_filter_evidence_not_overwritten_by_release_evidence():
    session = catalog_session(fixture())
    try:
        # Requesting all titles remains a catalog-only GET, never a detail GET.
        item = session.search("")[0]
        assert item.metadata["ebook_release_evidence"] == "ebooksgratuits.catalogue.parution"
        assert "categories" not in item.metadata
        assert "genres" not in item.metadata
    finally:
        session.close()
