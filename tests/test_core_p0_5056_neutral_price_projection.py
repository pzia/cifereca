"""#5056: descriptive commercial pricing never depends on a supplier name."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search_orchestration import _mirror_remote_results
from tests.test_core_p0_5056_catalogue_owned_attestation import (
    Storage, book, right,
)


def mirrored_state(item, rights=()):
    storage = Storage(list(rights))
    service = NS(storage=storage, current_identity=NS(identity_id="alice"))
    assert _mirror_remote_results(service, [item], identity_id="alice") == 1
    return storage.upserts[0]["local_state"]


@pytest.mark.parametrize("provider", [
    "kobo", "numilog", "fictional-a", "fictional-b", "unlisted",
])
def test_valid_explicit_price_is_descriptive_for_any_provider(provider):
    item = replace(book(provider), owned=False, price="12.99", currency="EUR")
    assert mirrored_state(item) == "available_to_buy"


@pytest.mark.parametrize("price", [None, "", "   ", 0, False, object()])
def test_no_valid_price_does_not_create_commercial_state(price):
    item = replace(book("unknown-provider"), owned=False, price=price)
    assert mirrored_state(item) == "remote_catalog"


@pytest.mark.parametrize("provider", ["kobo", "numilog", "fictional-a"])
def test_price_does_not_mint_a_canonical_owned_entitlement(provider):
    item = replace(book(provider), owned=True, price="9.99")
    assert mirrored_state(item) == "available_to_buy"
    assert mirrored_state(item, [right(provider)]) == "remote_owned"


def test_historical_paris_digital_without_price_keeps_borrow_label():
    item = replace(
        book("paris"), medium="digital", owned=False, price=None,
    )
    assert mirrored_state(item) == "available_to_borrow"


def test_price_does_not_override_attested_owned_right():
    item = replace(book("fictional-a"), price="9.99")
    assert mirrored_state(item, [right()]) == "remote_owned"
