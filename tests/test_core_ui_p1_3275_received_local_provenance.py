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
