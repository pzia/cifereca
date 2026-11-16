"""P0 #6366: an equal-looking replacement C receipt must repaint GTK.

The live N3 callback guards Library/Recents and Loan/History by the exact
Core receipt *object*, not by value-equality. MasterDetailPage.append-fast
must use the same authority or it leaves the old texture on-screen without
ever re-evaluating the revoked N3 predicate.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_c_local_origin_projection import VerifiedLocalPublicCLink
from cifereca.core.notice_c_borrower_public_projection import borrower_public_c_receipt
from cifereca.storage import Storage
from tests.test_core_p0_6366_borrower_public_c_lists_detail import (
    admit, bibliography, history, loan,
)
from tests.test_core_p1_3104_master_detail_visual_signature import (
    BookRow, page_with, production_methods,
)


def _row(key, document, *, proof_field, proof):
    row = BookRow(key, title="Le même livre")
    row._cover_document_ref = document
    setattr(row, proof_field, proof)
    row._public_row_content = (
        "same-presentation", key, row.title, document,
        getattr(proof, "epoch", None),
    )
    return row


@pytest.mark.parametrize("view", ["library", "recent"])
def test_equal_origin_link_rebuilt_by_core_must_replace_painted_row(view):
    doc = canonical_cover_document_ref("edition:identical-n3-bytes")
    original_link = VerifiedLocalPublicCLink(
        identity_id="alice", artifact_id="sha256:" + "a" * 64,
        placement_id="placement-1", document=doc, epoch=3,
        source_refs=("notice-c:v1:" + "b" * 64,),
    )
    newer_link = replace(original_link)
    assert newer_link == original_link and newer_link is not original_link
    key = "library:one" if view == "library" else "placement-1"
    before = _row(
        key, doc, proof_field="_cover_public_c_link", proof=original_link,
    )
    after = _row(
        key, doc, proof_field="_cover_public_c_link", proof=newer_link,
    )
    assert before._public_row_content == after._public_row_content
    same, show = production_methods()
    assert not same(before, after)

    page, store, selected, perf, _states, _counts = page_with(before)
    show(page, [after], total_count=1)
    assert store.items == [after] and store.items[0] is after
    assert selected.get_selected_item() is after
    assert store.splice_count == 1
    assert perf[-1][0] == "list.replace"

    # An unchanged *same* Core packet remains safe for append-fast.
    replay = _row(
        key, doc, proof_field="_cover_public_c_link", proof=newer_link,
    )
    assert same(after, replay)
    show(page, [replay, BookRow("next:2")], total_count=2)
    assert store.items[0] is after
    assert perf[-1][0] == "list.append-fast"


@pytest.mark.parametrize("make", [loan, history])
def test_equal_borrower_receipt_from_new_worker_forces_new_gtk_cell(
    tmp_path, make,
):
    db = Storage(tmp_path / ("borrower-" + make.__name__ + ".sqlite"))
    try:
        _, c = bibliography(db)
        private_before, private_after = make(), make()
        assert private_before == private_after
        assert private_before is not private_after
        assert admit(db, [private_before, private_after]) == 2
        old_receipt = borrower_public_c_receipt(
            private_before, identity_id="alice",
        )
        new_receipt = borrower_public_c_receipt(
            private_after, identity_id="alice",
        )
        assert old_receipt is not None and new_receipt is not None
        assert old_receipt == new_receipt
        assert old_receipt is not new_receipt
        assert old_receipt.source_item is not new_receipt.source_item

        old = _row(
            "borrower:original", c.document,
            proof_field="_cover_borrower_public_c_receipt",
            proof=old_receipt,
        )
        fresh = _row(
            "borrower:original", c.document,
            proof_field="_cover_borrower_public_c_receipt",
            proof=new_receipt,
        )
        same, show = production_methods()
        assert not same(old, fresh)
        page, store, selected, perf, _states, _counts = page_with(old)
        show(page, [fresh])
        assert store.items[0] is fresh
        assert selected.get_selected_item() is fresh
        assert perf[-1][0] == "list.replace"

        # An old packet can no longer own the new UI row; even an identical
        # N3 image and opaque C DocumentRef do not validate it.
        assert borrower_public_c_receipt(
            private_before, identity_id="alice",
        ) is old_receipt
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("field", [
    "_cover_public_c_link", "_cover_borrower_public_c_receipt",
])
def test_revoked_or_new_c_receipt_forces_rebind_without_source_url(field):
    same, show = production_methods()
    original = _row("stable-key", None, proof_field=field, proof=object())
    revoked = _row("stable-key", None, proof_field=field, proof=None)
    assert not same(original, revoked)
    assert not same(revoked, original)
    page, store, _selected, perf, _states, _counts = page_with(original)
    show(page, [revoked])
    assert store.items[0] is revoked
    assert perf[-1][0] == "list.replace"
