"""Exact, read-only canonical ADEPT retry-hold review (#7161)."""
from cifereca.core.canonical_adept_attempt import (
    _key, _value, describe_loan_adept_attempt, pending_loan_adept_attempt,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, register, store,
)


def _write(storage, source, value):
    with storage._lock, storage._connect() as db:
        db.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (_key(source), value))


def test_legacy_marker_is_classified_without_reset_or_raw_operation(store):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    assert describe_loan_adept_attempt(store, source, offer_id="loan-offer") == {
        "state": "none", "unlock_authorized": False,
    }
    marker = _value("loan-offer", "f" * 32)
    _write(store, source, marker)
    same = describe_loan_adept_attempt(store, source, offer_id="loan-offer")
    changed = describe_loan_adept_attempt(store, source, offer_id="new-offer")
    assert same == {"state": "held_same_offer", "unlock_authorized": False}
    assert changed == {"state": "held_other_offer", "unlock_authorized": False}
    assert pending_loan_adept_attempt(store, source)
    assert store.metadata_get(_key(source)) == marker
    assert "f" * 32 not in repr((same, changed))


def test_invalid_hold_still_blocks_and_does_not_grant_retry(store):
    source, _ = new_source(kind="loan", branch="dilicom")
    register(store, "A", "seller", "card-A")
    _write(store, source, "{not-json")
    assert describe_loan_adept_attempt(store, source, offer_id="x") == {
        "state": "held_invalid", "unlock_authorized": False,
    }
    assert pending_loan_adept_attempt(store, source)


def test_metadata_unreadable_fails_closed_without_exception():
    source, _ = new_source(kind="loan")

    class Unreadable:
        def metadata_get(self, _key):
            raise OSError("no access")

    assert describe_loan_adept_attempt(Unreadable(), source, offer_id="x") == {
        "state": "unreadable", "unlock_authorized": False,
    }
    assert pending_loan_adept_attempt(Unreadable(), source)
    assert describe_loan_adept_attempt(Unreadable(), source, offer_id="") == {
        "state": "invalid_offer", "unlock_authorized": False,
    }


def test_other_rights_and_invalid_sources_cannot_be_reinterpreted(store):
    purchase, _ = new_source(kind="purchase")
    assert describe_loan_adept_attempt(store, purchase, offer_id="x") == {
        "state": "invalid_source", "unlock_authorized": False,
    }
