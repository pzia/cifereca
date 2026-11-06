"""#4673: catalogue stock is not personal PNB loan eligibility.

Pure normalization fixture, no HTTP, loan POST, catalogue GET or session.
"""
from cifereca_paris_normalize import normalize_item_detail


def payload(*, can_loan=None, availability=1, explicit=True):
    holdings = {"Availability": availability, "HoldingLabel": "Disponible"}
    if explicit:
        holdings["CanLoan"] = can_loan
    return {"d": {"ItemHoldingsData": holdings, "fieldList": {"Title": ["Essai"]}}}


def test_explicit_negative_digital_canloan_beats_available_catalogue_stock():
    detail = normalize_item_detail("DILICOM", "same-edition", payload(can_loan=False))
    assert detail.availability_status == "unavailable"
    assert "non proposé" in detail.availability_label
    assert detail.availability_label != "Disponible"


def test_positive_and_missing_digital_canloan_are_not_automatically_denied():
    positive = normalize_item_detail("DILICOM", "same-edition", payload(can_loan=True))
    missing = normalize_item_detail("DILICOM", "same-edition", payload(explicit=False))
    for detail in (positive, missing):
        assert detail.availability_status == "available"
        # Supplier stock still cannot grant a personal borrow right.


def test_non_digital_stock_not_conflated_with_pnb_personal_canloan():
    physical = normalize_item_detail("CATALOGUE", "same-edition", payload(can_loan=False))
    assert physical.availability_status == "available"


def test_no_positive_stock_is_not_promoted_without_canloan():
    detail = normalize_item_detail(
        "DILICOM", "same-edition", payload(can_loan=None, availability=0)
    )
    assert detail.availability_status != "available"
