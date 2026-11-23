"""#4082 owner option A: offline, read-only BnF anti-replay proofs only.

No borrow POST, claim-v2 protocol, claim deletion or automatic rearm.
"""
from dataclasses import replace

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.storage import Storage
from tests.test_p1_4082_bnf_borrow_claim_observation import _account, _item, _observe


def test_collection_partition_and_catalogue_alias_keep_one_spent_claim(tmp_path):
    path = tmp_path / "collection-scopes.sqlite3"
    a = _account()
    first = replace(_item(a), source_collection="catalogue-a")
    same_edition_alias = replace(first, id="changed-list-row", title="Alternate title")
    other_collection = replace(first, source_collection="catalogue-b")
    key = borrow_claim_key(a, first)
    assert key == borrow_claim_key(a, same_edition_alias)
    assert key != borrow_claim_key(a, other_collection)
    db = Storage(path)
    try:
        assert db.loan_borrow_claim_once(key, "initial-manual-click")[0]
        before = db.metadata_get(key)
        state = _observe(db, a, same_edition_alias)
        assert state.state == "request_sent"
        assert state.claim_consumed is True
        assert state.retry_authorized is False
        assert db.metadata_get(key) == before
        assert _observe(db, a, other_collection).state == "no_checkpoint"
        assert db.loan_borrow_claim_once(
            borrow_claim_key(a, same_edition_alias), "duplicate-click",
        )[0] is False
    finally:
        db.close()
    db = Storage(path)
    try:
        assert _observe(db, a, same_edition_alias).state == "request_sent"
        assert _observe(db, a, same_edition_alias).retry_authorized is False
        assert db.loan_borrow_claim_once(key, "retry-after-restart")[0] is False
    finally:
        db.close()


def test_ambiguous_submitted_post_remains_consumed_and_read_only(tmp_path):
    path = tmp_path / "ambiguous.sqlite3"
    a = _account()
    item = _item(a)
    key = borrow_claim_key(a, item)
    db = Storage(path)
    try:
        assert db.loan_borrow_claim_once(key, "possibly-submitted")[0]
        db.loan_borrow_finish(
            key, "possibly-submitted", status="verification_unavailable",
            accepted=False, verified_loan_id=None,
        )
        before = db.metadata_get(key)
        observation = _observe(db, a, item)
        assert observation.state == "verification_unavailable"
        assert observation.claim_consumed is True
        assert observation.provider_verified is False
        assert observation.retry_authorized is False
        assert db.metadata_get(key) == before
        assert db.loan_borrow_claim_once(key, "another-click")[0] is False
    finally:
        db.close()
    db = Storage(path)
    try:
        assert _observe(db, a, item).retry_authorized is False
        assert db.loan_borrow_claim_once(key, "post-restart-click")[0] is False
    finally:
        db.close()
