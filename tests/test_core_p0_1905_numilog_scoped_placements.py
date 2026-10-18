"""Scope-sensitive Numilog preview reconciliation (#1905).

Two distinct reader libraries can hold identical EPUB bytes, so their SHA
artifact ID is shared. Only an exact identity/provider/purchase placement can
confirm that the currently previewed reader already has the book.
"""
from __future__ import annotations

from tests.test_core_p0_1905_numilog_offline_preview import (
    _broker, _ledger, _owned, _preview,
)
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


def test_shared_sha_across_two_identities_does_not_create_false_ambiguity(tmp_path):
    service = _service(tmp_path)
    try:
        a, session_a, calls_a = _connect(
            service, identity="default", credential="reader-one", number=0,
        )
        b, session_b, calls_b = _connect(
            service, identity="alex", credential="reader-two", number=0,
        )
        first, first_eid = _owned(service, a, lid="1", variant="a" * 16)
        second, second_eid = _owned(service, b, lid="2", variant="b" * 16)
        # Exact same content but independent owned purchases and paths.
        first_path = _ledger(service, a, first, first_eid)
        second_path = _ledger(
            service, b, second, second_eid, wrong_scope=True,
        )
        assert first_path.read_bytes() == second_path.read_bytes()
        assert first_path != second_path
        session_a.library = session_b.library = lambda: 1 / 0
        broker, a_id = _broker(service, a)
        b_id = broker._source_id(b)
        # Neither the current identity nor a foreign placement may impact
        # an explicit account-scoped offline preview.
        service.current_identity = service.identity_by_id("alex")
        for sid in (a_id, b_id):
            preview = _preview(broker, sid, verify=True)
            assert preview.ok and preview.changed == 0
            assert preview.facts["counts"]["owned_purchases"] == 1
            assert preview.facts["counts"]["already_local_verified"] == 1
            assert preview.facts["counts"]["needs_reconciliation"] == 0
            assert preview.facts["acquisition_authorized"] is False
        assert calls_a == calls_b == []
    finally:
        service.close()


def test_duplicate_placements_inside_same_purchase_remain_ambiguous(tmp_path):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        local = _ledger(service, account, item, eid)
        digest = __import__("hashlib").sha256(local.read_bytes()).hexdigest()
        # A second placement for the *same* identity/edition must not be
        # accepted by selecting an arbitrary file, even if another is valid.
        service.storage.placement_put({
            "artifact_id": "sha256:" + digest,
            "path": str(local.with_name("duplicate.epub")),
            "state": "present", "identity_id": account.identity_id,
            "provider_id": "numilog", "source_item_id": item,
        })
        session.library = lambda: 1 / 0
        broker, sid = _broker(service, account)
        outcome = _preview(broker, sid, verify=True)
        assert outcome.ok
        assert outcome.facts["counts"]["already_local_verified"] == 0
        assert outcome.facts["counts"]["needs_reconciliation"] == 1
        assert outcome.facts["acquisition_authorized"] is False
        assert calls == []
    finally:
        service.close()
