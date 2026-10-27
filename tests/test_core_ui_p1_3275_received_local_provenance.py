"""#3275: Library must distinguish received bytes from the recipient's rights.

GTK-free real Library projector, no supplier calls or local state mutations.
"""
from __future__ import annotations

from cifereca.gui.library_rows import project_library_rows


def _render(row):
    rows, _ = project_library_rows(
        [row], row_factory=lambda *args: args,
        provider_display_name=lambda provider: provider,
        availability_label=lambda item: "Local" if item.get("local") else "Connu",
        backup_status_label=lambda status: status,
        format_date=lambda due: due,
    )
    assert len(rows) == 1
    return rows[0][3]


def _received(**kwargs):
    row = {
        "key": "artifact:synthetic", "title": "Un livre",
        "author": "Auteur", "provider_id": "kobo",
        "identity_id": "bob", "local": True, "state": "local",
        "metadata": {
            "transfer_kind": "local_copy",
            "file_origin_identity_id": "alice",
            "materialized_from": "placement-A",
        },
    }
    row.update(kwargs)
    return row


def test_local_kobo_copy_labels_original_identity_without_claiming_b_right():
    subtitle = _render(_received())
    assert "Local" in subtitle
    assert "Fichier reçu de alice" in subtitle
    assert "aucun droit transmis" in subtitle
    assert "Achat Kobo" not in subtitle


def test_independent_b_entitlement_does_not_become_transferred_a_right():
    item = _received(
        entitlement_type="owned",
        possessions=[{
            "identity_id": "bob", "provider_id": "kobo",
            "item_id": "book-1", "kind": "owned",
            "account_id": "kobo-B", "state": "active",
        }],
    )
    subtitle = _render(item)
    assert "Fichier reçu de alice" in subtitle
    assert "aucun droit transmis" in subtitle
    assert "absence de droit chez bob" not in subtitle


def test_only_exact_recorded_interidentity_provenance_earns_label():
    cases = [
        _received(local=False),
        _received(metadata={"transfer_kind": "local_copy",
                            "file_origin_identity_id": "alice"}),
        _received(metadata={"transfer_kind": "downloaded",
                            "materialized_from": "placement-A",
                            "file_origin_identity_id": "alice"}),
        _received(metadata={"transfer_kind": "local_copy",
                            "materialized_from": "placement-A",
                            "file_origin_identity_id": "bob"}),
        _received(metadata={"transfer_kind": "local_copy",
                            "materialized_from": "placement-A",
                            "file_origin_identity_id": "x" * 200}),
        _received(metadata={"source_credential": "kobo-A"}),
    ]
    for case in cases:
        assert "Fichier reçu" not in _render(case)
        assert "droit transmis" not in _render(case)



def _b_attested_right(**overrides):
    right = {
        "identity_id": "bob", "provider_id": "kobo",
        "item_id": "book-1", "kind": "owned", "state": "active",
        "account_id": "B-own-account", "entitlement_id": "B-own-entitlement",
        "expires_at": None,
    }
    right.update(overrides)
    return right


def test_received_file_and_recipient_independent_purchase_are_two_distinct_facts():
    row = _received(
        item_id="book-1", source_item_id="book-1",
        possessions=[_b_attested_right()],
    )
    subtitle = _render(row)
    assert "Fichier reçu de alice · aucun droit transmis" in subtitle
    assert "Achat Kobo indépendant pour bob" in subtitle
    assert "Lié" not in subtitle
    assert "téléchargé" not in subtitle
    assert "Achat Kobo indépendant pour alice" not in subtitle
    assert "Achat Kobo indépendant" not in _render(_received(
        item_id="book-1", possessions=[],
    ))


def test_kobo_recipient_right_requires_exact_source_account_entitlement_and_identity():
    baseline = _received(item_id="book-1", possessions=[_b_attested_right()])
    assert "Achat Kobo indépendant pour bob" in _render(baseline)
    for altered in (
        {"identity_id": "alice"},
        {"provider_id": "numilog"},
        {"item_id": "other-edition"},
        {"kind": "loan"},
        {"state": "archived"},
        {"state": "revoked"},
        {"account_id": None},
        {"account_id": ""},
        {"entitlement_id": None},
        {"entitlement_id": ""},
        {"expires_at": "2027-01-01"},
    ):
        row = _received(item_id="book-1", possessions=[_b_attested_right(**altered)])
        subtitle = _render(row)
        assert "Fichier reçu de alice" in subtitle
        assert "Achat Kobo indépendant" not in subtitle, altered


def test_two_recipient_accounts_are_ambiguous_not_automatically_selected():
    row = _received(
        item_id="book-1", possessions=[
            _b_attested_right(),
            _b_attested_right(account_id="B-second-account",
                              entitlement_id="B-second-entitlement"),
        ],
    )
    assert "Fichier reçu de alice" in _render(row)
    assert "Achat Kobo indépendant" not in _render(row)


def test_source_right_of_sender_never_becomes_recipient_purchase():
    row = _received(
        item_id="book-1", possessions=[_b_attested_right(
            identity_id="alice", account_id="A-own-account",
            entitlement_id="A-own-entitlement",
        )],
    )
    subtitle = _render(row)
    assert "Fichier reçu de alice" in subtitle
    assert "Achat Kobo indépendant" not in subtitle


def test_unrelated_row_does_not_claim_recipient_kobo_purchase():
    for row in (
        _received(item_id="book-1", local=False, possessions=[_b_attested_right()]),
        _received(item_id="book-1", provider_id="numilog",
                  possessions=[_b_attested_right()]),
        _received(item_id="book-1", metadata={},
                  possessions=[_b_attested_right()]),
        _received(item_id=None, possessions=[_b_attested_right()]),
    ):
        assert "Achat Kobo indépendant" not in _render(row)
