"""P1 #1867: one physical edition may have purchases, loans and unknown rights.

Only scoped entitlement type/state controls the wording. Neither a store
provider ID nor a local file is itself an ownership/loan attestation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.library_detail_controller import possession_usage_label
from tests.test_core_ui_1129_library_detail_controller import _context


@pytest.mark.parametrize("kind,state,expected", [
    ("owned", "active", "Achat possédé en ligne : Kobo"),
    ("owned", "archived", "Achat archivé : Kobo"),
    ("loan", "active", "Prêt de bibliothèque actif : Kobo"),
    ("loan", "expired", "Prêt de bibliothèque non actif : Kobo"),
    ("loan", "returned", "Prêt de bibliothèque non actif : Kobo"),
    ("loan", "inactive", "Prêt de bibliothèque non actif : Kobo"),
    ("owned", "returned", "Droit distant non qualifié : Kobo"),
    ("loan", "archived", "Droit distant non qualifié : Kobo"),
    ("owned", None, "Droit distant non qualifié : Kobo"),
    ("loan", None, "Droit distant non qualifié : Kobo"),
    (None, "active", "Droit distant : Kobo"),
    ("catalog", "active", "Droit distant : Kobo"),
    ("OWNED", "active", "Droit distant : Kobo"),
    ("owned", True, "Droit distant non qualifié : Kobo"),
    (["owned"], ["active"], "Droit distant : Kobo"),
    ("loan", {"state": "active"}, "Droit distant non qualifié : Kobo"),
])
def test_exact_entitlement_kind_and_state_only(kind, state, expected):
    assert possession_usage_label({"kind": kind, "state": state}, "Kobo") == expected


def test_untyped_and_legacy_possessions_do_not_become_purchases():
    assert possession_usage_label({}, "Paris") == "Droit distant : Paris"
    assert possession_usage_label(None, "Paris") == "Droit distant : Paris"
    assert possession_usage_label("not a row", "Numilog") == "Droit distant : Numilog"


def test_same_provider_is_not_sufficient_for_a_purchased_badge():
    for provider in ("Numilog", "Kobo", "Paris"):
        assert "Achat" not in possession_usage_label(
            {"kind": "loan", "state": "active"}, provider
        )
        assert "Prêt" not in possession_usage_label(
            {"kind": "owned", "state": "active"}, provider
        )


def test_library_multi_identity_detail_keeps_loan_purchase_archive_local_separate():
    item = {
        "key": "edition-1",
        "title": "Édition partagée",
        "provider_id": "kobo",
        "identity_id": "alice",
        "state": "local",
        "local": True,
        "placements": [
            {"identity_id": "alice", "storage_target_id": "Books"},
        ],
        "possessions": [
            {"identity_id": "alice", "provider_id": "kobo",
             "kind": "owned", "state": "active"},
            {"identity_id": "bob", "provider_id": "numilog",
             "kind": "owned", "state": "archived"},
            {"identity_id": "bob", "provider_id": "paris",
             "kind": "loan", "state": "active"},
            {"identity_id": "alice", "provider_id": "kobo",
             "kind": "owned", "state": "active"},  # duplicate must be deduplicated
        ],
    }
    window, events, projected, loans, call = _context(item)
    call(NS(key="edition-1"))
    groups = [(name, values) for event, name, values in events if event == "group"]
    assert groups == [(
        "Présence / Bibliothèques / Sources",
        [
            ("Alice", "Stockage : Books"),
            ("Alice", "Achat possédé en ligne : Kobo"),
            ("Bob", "Achat archivé : numilog"),
            ("Bob", "Prêt de bibliothèque actif : Paris"),
        ],
    )]
    assert len(projected) == 1
    assert not any("Télécharger" in str(event) for event in events)
    window.service.can_download_loan.assert_not_called()
    loans.assert_not_called()


def test_malformed_or_legacy_stored_rows_stay_conservatively_unqualified():
    item = {
        "key": "edition-2", "title": "Notice", "provider_id": "numilog",
        "possessions": [
            {"identity_id": "alice", "provider_id": "numilog",
             "kind": "owned", "state": None},
            {"identity_id": "bob", "provider_id": "numilog",
             "kind": "catalog", "state": "active"},
            {"identity_id": "alice", "provider_id": "paris"},
        ],
    }
    _window, events, _projected, _loans, call = _context(item)
    call(NS(key="edition-2"))
    group = next(row for row in events if row[0] == "group")
    assert group[2] == [
        ("Alice", "Droit distant non qualifié : numilog"),
        ("Bob", "Droit distant : numilog"),
        ("Alice", "Droit distant : Paris"),
    ]
    assert all("Achat" not in label and "Prêt" not in label for _owner, label in group[2])
