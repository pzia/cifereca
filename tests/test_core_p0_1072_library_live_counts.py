"""Current deduplicated Library state, not a total inventory or I/O."""
from dataclasses import replace

from cifereca.core.notice_c_purchase_summary import projected_purchase_counts
from cifereca.core.notice_c_purchase_library_projection import PurchaseLibraryNotice, PurchaseLibraryStatus
from cifereca.core.notice_c_purchase_descriptive_read import PurchaseDescriptiveNotice
from cifereca.gui.library_documentary_summary import documentary_summary
from cifereca.plugin_api import ProviderRightsScope, SourceReference


def remote(key, source, *, state="pending"):
    scope = source.scope
    row = {
        "key": key, "identity_id": scope.identity_id, "provider_id": scope.provider_id,
        "item_id": source.edition_id, "source": "private_catalog_observation",
        "state": "remote_catalog", "local": False,
        "source_refs": [{"kind": "purchase", "identity_id": scope.identity_id,
                         "provider_id": scope.provider_id, "credential_id": scope.credential_id,
                         "account_id": scope.account_id, "item_id": source.edition_id}],
    }
    if state == "available":
        row["_purchase_library_notice"] = PurchaseLibraryNotice(
            key, source, PurchaseDescriptiveNotice(None, 1, 100, ()))
    elif state:
        row["_purchase_library_status"] = PurchaseLibraryStatus(key, source, state, 0)
    return row


def source(owner="A", edition="one"):
    return SourceReference(ProviderRightsScope(owner, "seller", "card", "account"),
                           "purchases", edition, edition, "purchase")


def test_counts_dedupe_exact_a_and_never_count_unqualified_or_foreign_rows():
    a, b, c = (source(edition=x) for x in ("one", "two", "three"))
    entries = [remote("a", a, state="available"), remote("a2", a, state="available"),
               remote("b", b), remote("c", c, state=None),
               remote("foreign", source("B"), state="failed")]
    assert projected_purchase_counts(entries, identity_id="A") == {"available": 1, "pending": 1}
    assert "1 fiches disponibles" in documentary_summary(projected_purchase_counts(entries, identity_id="A"))
    assert "1 en attente" in documentary_summary(projected_purchase_counts(entries, identity_id="A"))
    # Local alias and remote card are one purchase, not two copies.
    local = {"key": "local", "identity_id": "A", "local": True, "state": "local",
             "artifact_id": "artifact", "placement_id": "placement",
             "_purchase_library_notice": replace(entries[0]["_purchase_library_notice"],
                 key="local", placement=("artifact", "placement"))}
    assert projected_purchase_counts(entries + [local], identity_id="A") == {"available": 1, "pending": 1}
    # Contradiction across aliases removes only the affected source.
    assert projected_purchase_counts(entries + [remote("a3", a, state="failed")],
                                     identity_id="A") == {"pending": 1}
    # Duplicate list keys are ambiguous, not an extra source.
    assert projected_purchase_counts(entries + [remote("b", b)], identity_id="A") == {"available": 1}
    assert projected_purchase_counts(entries, identity_id="C") == {}
    assert documentary_summary({}) == "" and documentary_summary({"pending": True}) == ""


def test_closed_live_states_and_historical_counters_stay_distinct():
    states = ["pending", "running", "retry_wait", "failed", "proof_unavailable", "paused"]
    rows = [remote(str(i), source(edition=str(i)), state=state) for i, state in enumerate(states)]
    counts = projected_purchase_counts(rows, identity_id="A")
    assert counts == dict.fromkeys(states, 1)
    assert "preuves à revalider" in documentary_summary(counts)
    assert documentary_summary({"requested": 1}) == ""
    assert projected_purchase_counts(rows, identity_id=None) == {}


def test_available_with_pending_refresh_counts_once_and_exposes_subset():
    a, b = source(), source(edition="two")
    row = remote("first", a, state="available")
    row["_purchase_library_status"] = PurchaseLibraryStatus("first", a, "retry_wait", 1)
    second = remote("second", b, state="pending")
    counts = projected_purchase_counts([row, second], identity_id="A")
    assert counts == {"available": 1, "refresh_retry_wait": 1, "pending": 1}
    assert "Fiches conservées, dont actualisation : 1 différées" in documentary_summary(counts)
    assert documentary_summary({"available": 1, "refresh_pending": 2}) == ""
    conflicting = remote("conflicting", a, state="available")
    assert projected_purchase_counts([row, conflicting, second], identity_id="A") == {"pending": 1}
