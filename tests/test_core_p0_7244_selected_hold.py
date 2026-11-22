"""#7244 exact hold selection is a private negative witness, never a permit."""
import pytest

from cifereca.core import adept_historical_maintenance as review
from cifereca.core import canonical_adept_attempt as marker
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from tests.test_core_p0_976_historical_maintenance import _setup, store, new_source, BYTES


@pytest.mark.parametrize("change", ["none", "aba", "forged", "scope", "wrong_index", "unconfirmed"])
def test_selected_historical_hold_is_bound_to_exact_page(store, tmp_path, change):
    service, first, path, _ = _setup(store, tmp_path)
    second, snapshot = new_source(kind="loan", branch="loans", right="another")
    persist_attested_source_references(store, snapshot)
    before = marker._value("offer", "a" * 32)
    for source in (first, second):
        store.metadata_set(marker._key(source), before)
    proof = marker._unsubmitted_value(second, before, "core_pre_dispatch_refusal")
    store.metadata_set(marker._unsubmitted_key(second), proof)
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request)
    index = next(i for i, row in enumerate(page["rows"])
                 if row["provider_loan_id"] == second.right_id)
    selected = review.select_historical_hold(
        service, request, page, ordinal=index, confirmed=True)
    assert selected is not None and selected.source == second
    assert selected.current() and not selected.unlock_authorized
    assert selected.witness.state == "non_submission_recorded"
    assert before not in repr(selected) and proof not in repr(selected)
    confirmed = True
    if change == "aba":
        store.metadata_set(marker._key(second), marker._value("other", "b" * 32))
        store.metadata_set(marker._key(second), before)
    elif change == "forged":
        page = dict(page, rows=tuple(dict(row, provider_loan_id="different")
                                     for row in page["rows"]))
    elif change == "scope":
        service._account_context_epoch += 1
    elif change == "wrong_index":
        index = True
    elif change == "unconfirmed":
        confirmed = False
    result = review.select_historical_hold(
        service, request, page, ordinal=index, confirmed=confirmed)
    assert (result is not None) == (change == "none")
    if change in ("aba", "scope"):
        assert not selected.current()
    assert store.metadata_get(marker._key(first)) == before
    assert store.metadata_get(marker._key(second)) == before
    assert store.metadata_get(marker._unsubmitted_key(second)) == proof
    assert path.read_bytes() == BYTES
