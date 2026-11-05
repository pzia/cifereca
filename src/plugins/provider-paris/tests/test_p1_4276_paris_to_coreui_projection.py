"""#4276: real Paris normalization bridges to provider-neutral search filters.

A public catalogue notice does not attest a user loan, a file download, an
offer or a local placement. These tests require neither GTK nor a Paris login.
"""
from __future__ import annotations

from cifereca.core.document_filter import (
    classification_from_row, classified_content_type, search_medium_label,
)
from cifereca.gui.query_model import QueryState, result_row
from cifereca_paris_normalize import normalize_search_result


def notice(item_id, kind, code, base, *, scenario="CATALOGUE", format=None):
    payload = {
        "Resource": {
            "RscId": item_id, "RscBase": base,
            "Type": kind, "Frmt": code,
            "Ttl": "Fictional " + item_id,
        },
    }
    if format:
        payload["Resource"]["MediaFormat"] = format
    row = normalize_search_result(payload, scenario)
    assert row is not None
    return row


def typed_rows():
    return [
        notice("book", "Livre", "MONO", "SYRACUSE"),
        notice("comic", "BD", "BDTP", "SYRACUSE"),
        notice("music", "Musique", "AUDI", "SYRACUSE"),
        notice("film", "Film", "AUVI", "SYRACUSE"),
        notice("periodical", "Revue", "PERI", "SYRACUSE"),
        notice("ebook", "Livre numérique", "MONE", "DILICOM",
               format="application/epub+zip"),
        notice("storyplayr", "Livre numérique", "MONE", "STORYPLAYR"),
        notice("unknown", "Future documentary medium", "UNKNOWN", "OTHER",
               scenario="EBOOKS"),
    ]


def ids(rows):
    return [row["record_id"] for row in rows]


def test_real_paris_notices_flow_into_shared_coreui_documentary_filters():
    rows = [result_row(row) for row in typed_rows()]
    assert ids(rows) == [
        "book", "comic", "music", "film",
        "periodical", "ebook", "storyplayr", "unknown",
    ]
    assert ids(QueryState(scope="physical").filter(rows)) == [
        "book", "comic", "music", "film", "periodical",
    ]
    assert ids(QueryState(scope="digital").filter(rows)) == [
        "ebook", "storyplayr",
    ]
    assert ids(QueryState(scope="unknown").filter(rows)) == ["unknown"]
    assert ids(QueryState(types=("printed_book",)).filter(rows)) == ["book"]
    assert ids(QueryState(types=("comics",)).filter(rows)) == ["comic"]
    assert ids(QueryState(types=("music",)).filter(rows)) == ["music"]
    assert ids(QueryState(types=("video",)).filter(rows)) == ["film"]
    assert ids(QueryState(types=("periodical",)).filter(rows)) == ["periodical"]
    assert ids(QueryState(types=("ebook",)).filter(rows)) == [
        "ebook", "storyplayr",
    ]
    assert ids(QueryState(types=("epub",)).filter(rows)) == ["ebook"]
    assert ids(QueryState(types=("unknown",)).filter(rows)) == ["unknown"]


def test_gui_labels_preserve_document_kind_without_equating_films_to_books():
    expected = {
        "book": ("PRINTED_BOOK", "Livre imprimé"),
        "comic": ("COMIC", "Bande dessinée · physique"),
        "music": ("MUSIC", "Musique · physique"),
        "film": ("VIDEO", "Film / vidéo · physique"),
        "periodical": ("PERIODICAL", "Revue · physique"),
        "ebook": ("EBOOK", "Livre numérique"),
        "storyplayr": ("EBOOK", "Livre numérique"),
        "unknown": ("UNKNOWN", "Support indéterminé"),
    }
    for original in typed_rows():
        ui_row = result_row(original)
        proof = classification_from_row(ui_row)
        assert proof is not None
        typ, label = expected[original.record_id]
        assert proof.meta_type == typ
        assert search_medium_label(proof) == label
        assert classified_content_type(proof) != "physical"
        assert ui_row["state"] == "remote_catalog"


def test_documentary_type_does_not_create_ownership_or_library_download():
    for original in typed_rows():
        gui = result_row(original)
        assert gui["owned"] is None
        assert gui["downloadable"] is None
        assert gui["download_url"] is None
        assert gui["state"] != "remote_owned"
        proof = classification_from_row(gui)
        assert proof is not None
        assert proof.offered_file_formats == ()
        assert not hasattr(proof, "rights")
        assert not hasattr(proof, "placement")
        assert not hasattr(proof, "sha256")


def test_search_route_does_not_override_physical_film_or_unknown_support():
    film = notice("film", "Film", "AUVI", "SYRACUSE", scenario="EBOOKS")
    unknown = notice(
        "unknown", "Future media", "XXX", "OTHER", scenario="EBOOKS",
    )
    rows = [result_row(film), result_row(unknown)]
    assert ids(QueryState(scope="digital").filter(rows)) == []
    assert ids(QueryState(scope="physical").filter(rows)) == ["film"]
    assert ids(QueryState(scope="unknown").filter(rows)) == ["unknown"]
    assert all(row["document_classification"] is not None for row in rows)


def test_file_format_is_known_only_when_explicit_public_manifestation():
    plain = notice("plain", "Livre numérique", "MONE", "DILICOM")
    coded = notice(
        "coded", "Livre numérique", "MONE", "DILICOM",
        format="application/epub+zip",
    )
    assert plain.format is None and coded.format == "EPUB"
    as_rows = [result_row(plain), result_row(coded)]
    assert ids(QueryState(types=("epub",)).filter(as_rows)) == ["coded"]
    for row in as_rows:
        assert row["state"] == "remote_catalog"
        assert row["owned"] is None and row["downloadable"] is None
