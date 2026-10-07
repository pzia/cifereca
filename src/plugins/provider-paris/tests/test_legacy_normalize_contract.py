from __future__ import annotations

from cifereca_paris_normalize import microsoft_date_to_iso, normalize_history, normalize_loan


def test_microsoft_date():
    value = microsoft_date_to_iso("/Date(1791955718733+0200)/")
    assert value and value.startswith("2026-")


def test_digital_loan():
    loan = normalize_loan(
        {
            "Id": "x",
            "RecordId": "9780000000000",
            "Title": "Livre",
            "WhenBack": "/Date(1791955718733+0200)/",
            "CanRenew": False,
            "AdditionalProperties": {
                "AccessMedium": "DOWNLOAD",
                "CanReturn": True,
                "HasBeenRenewed": False,
                "EbookUrl": "https://example.invalid/x",
            },
        },
        "DILICOM",
    )
    assert loan.medium == "digital"
    assert loan.can_return
    assert loan.access_medium == "DOWNLOAD"


def test_history_keeps_default_thumbnail_separate():
    item = normalize_history(
        {
            "Id": "h",
            "Title": "Titre",
            "LoanDate": "/Date(1750000000000+0200)/",
            "ThumbnailUrl": None,
            "DefaultThumbnailUrl": "https://example/default.png",
            "AdditionalProperties": {"Auteur": "A", "Annee": "2020"},
        },
        "SYRACUSE",
    )
    assert item.author == "A"
    assert item.year == "2020"
    assert item.thumbnail_url is None
    assert item.default_thumbnail_url == "https://example/default.png"
