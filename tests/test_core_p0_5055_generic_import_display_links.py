"""P0 #5055: observed import provenance is a read-only display hint only."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.observed_import_provenance import observed_import_links
from cifereca.core.local_import_display_links import observed_import_display_links


SCOPE = {
    "identity_id": "alice", "credential_id": "card-a",
    "account_id": "borrower-a", "loan_id": "loan-a", "item_id": "edition-a",
}
KEY = ("alice", "card-a", "borrower-a", "loan-a", "edition-a")


def view(rows, placements=None, keys=None, *, provider="future-lender",
         field="observed_loan"):
    from cifereca.core.observed_loan_scope import scope_key
    db = NS(acquisitions=lambda **args: (
        rows if args == {"provider_id": "manual", "status": "complete"} else []
    ))
    return observed_import_display_links(
        db, placements if placements is not None else [{
            "path": "/library/book.epub", "placement_id": "place-1",
            "identity_id": "alice", "provider_id": provider,
            "artifact_id": "sha256:abc",
        }], keys if keys is not None else {
            ("place-1", "sha256:abc"): "local-1",
        }, provenance_field=field, placement_provider_id=provider,
        validate_scope=scope_key,
    )


def row(*, field="observed_loan"):
    return {
        "provider_id": "manual", "identity_id": "alice",
        "entitlement_id": None, "strategy": "manual_acsm",
        "artifact_id": "sha256:abc",
        "metadata": {"path": "/library/book.epub", field: SCOPE},
    }


def test_synthetic_provider_exact_observation_is_display_only():
    assert view([row()]) == {KEY: "local-1"}


def test_bnf_compatibility_uses_same_generic_projection():
    row_bnf = row(field="bnf_observed_loan")
    placements = [{
        "path": "/library/book.epub", "placement_id": "place-1",
        "identity_id": "alice", "provider_id": "bnf-pret",
        "artifact_id": "sha256:abc",
    }]
    keys = {("place-1", "sha256:abc"): "local-1"}
    db = NS(acquisitions=lambda **kwargs: [row_bnf])
    assert observed_import_links(
        db, placements, keys, provider_id='bnf-pret',
        provenance_field='bnf_observed_loan',
    ) == {KEY: "local-1"}
    assert view([row_bnf], placements, keys, provider="bnf-pret") == {}


@pytest.mark.parametrize("field,value", [
    ("identity_id", "bob"),
    ("credential_id", ""),
    ("loan_id", None),
    ("unknown", "extra"),
])
def test_observed_scope_fails_closed_on_foreign_or_malformed_data(field, value):
    updated = dict(SCOPE)
    updated[field] = value
    bad = row()
    bad["metadata"]["observed_loan"] = updated
    assert view([bad]) == {}


@pytest.mark.parametrize("change,value", [
    ("identity_id", "other"), ("strategy", "other"),
    ("entitlement_id", "attached-right"),
    ("artifact_id", "sha256:foreign"),
])
def test_conflicting_acquisition_never_links(change, value):
    data = row()
    data[change] = value
    assert view([data]) == {}


def test_foreign_or_duplicated_placements_are_not_display_proof():
    place = {"path": "/library/book.epub", "placement_id": "place-1",
             "identity_id": "alice", "provider_id": "future-lender",
             "artifact_id": "sha256:abc"}
    assert view([row()], [dict(place, provider_id="other")]) == {}
    assert view([row()], [dict(place, identity_id="bob")]) == {}
    assert view([row()], [place, dict(place, placement_id="place-2")]) == {}


def test_ambiguous_multiple_local_keys_refuse_mapping():
    first = row()
    second = row()
    second["metadata"] = dict(second["metadata"], path="/library/other.epub")
    places = [
        {"path": "/library/book.epub", "placement_id": "place-1",
         "identity_id": "alice", "provider_id": "future-lender",
         "artifact_id": "sha256:abc"},
        {"path": "/library/other.epub", "placement_id": "place-2",
         "identity_id": "alice", "provider_id": "future-lender",
         "artifact_id": "sha256:def"},
    ]
    second["artifact_id"] = "sha256:def"
    keys = {
        ("place-1", "sha256:abc"): "local-1",
        ("place-2", "sha256:def"): "local-2",
    }
    assert view([first, second], places, keys) == {}


@pytest.mark.parametrize("invalid", [{"not": "a path"}, ["unhashable"], None])
def test_hostile_metadata_path_never_raises_or_grants(invalid):
    data = row()
    data["metadata"]["path"] = invalid
    assert view([data]) == {}
