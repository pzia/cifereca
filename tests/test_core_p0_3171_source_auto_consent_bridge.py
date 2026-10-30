"""#3171: source/type auto-download grants the canonical A→B executor, not UI rights.

Real SQLite, exact provider/credential/account scope, valid opt-in and
re-evaluation at the SQLite publication boundary. No provider HTTP.
"""
from dataclasses import replace

import pytest

from cifereca.core import source_auto_consent_bridge as bridge
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.source_acquisition_consent import (
    consent_current_in_transaction, read_source_acquisition_consent,
    set_source_acquisition_consent, canonical_acquisition_choices,
)
from tests.test_core_p0_3171_canonical_sync_stage import rig


@pytest.fixture
def policy(rig, monkeypatch):
    service, db, root = rig
    # The real Numilog/BnF policies use the same bridge contract, but these
    # fake-provider test rows isolate Core from any installed plugin.
    monkeypatch.setitem(bridge._POLICIES, ("seller-a", "purchase"), (
        "sync.test.provider.auto.", "1", None,
    ))
    persist_attested_source_references(db, service.snapshot)
    key = "sync.test.provider.auto." + bridge._source_id(service.source.scope)
    return service, db, key


def test_auto_on_grants_all_current_positive_offers_without_per_right_click(policy):
    service, db, key = policy
    other = replace(service.snapshot.rights[0],
                    edition_id="another-edition", right_id="another-right")
    persist_attested_source_references(
        db, replace(service.snapshot,
                    rights=(service.snapshot.rights[0], other)),
    )
    second = replace(service.source,
                     edition_id="another-edition", right_id="another-right")
    assert read_source_acquisition_consent(service, service.account) == frozenset()
    db.metadata_set(key, "1")
    assert read_source_acquisition_consent(service, service.account) == frozenset({
        service.source, second,
    })
    # Once a source-wide switch is configured, no one-by-one authorization UI.
    assert canonical_acquisition_choices(service, service.account) == ()
    with db._lock, db._connect() as conn:
        assert consent_current_in_transaction(conn, service.source)
        assert consent_current_in_transaction(conn, second)


def test_auto_off_revokes_even_an_earlier_exact_grant(policy):
    service, db, key = policy
    set_source_acquisition_consent(
        service, service.account, service.source, allowed=True,
    )
    assert read_source_acquisition_consent(service, service.account) == frozenset({
        service.source,
    })
    db.metadata_set(key, "1")
    assert read_source_acquisition_consent(service, service.account)
    db.metadata_set(key, "0")
    assert read_source_acquisition_consent(service, service.account) == frozenset()
    with db._lock, db._connect() as conn:
        assert not consent_current_in_transaction(conn, service.source)
    assert canonical_acquisition_choices(service, service.account) == ()


@pytest.mark.parametrize("tamper", ["disabled", "other_account", "inactive", "historical"])
def test_source_grant_cannot_bypass_exact_account_binding_and_positive_A(policy, tamper):
    service, db, key = policy
    db.metadata_set(key, "1")
    with db._lock, db._connect() as conn:
        if tamper == "disabled":
            conn.execute("UPDATE provider_connections SET enabled=0")
        elif tamper == "other_account":
            conn.execute("UPDATE provider_accounts SET account_id='another'")
        elif tamper == "inactive":
            conn.execute("UPDATE source_references SET state='inactive'")
        else:
            conn.execute("UPDATE source_references SET evidence_kind='historical_receipt'")
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_unconfigured_provider_type_and_legacy_per_right_are_unchanged(policy):
    service, db, _key = policy
    assert bridge.configured_kind("seller-a", "loan") is False
    set_source_acquisition_consent(
        service, service.account, service.source, allowed=True,
    )
    assert read_source_acquisition_consent(service, service.account) == frozenset({
        service.source,
    })
    assert len(canonical_acquisition_choices(service, service.account)) == 1


def test_numilog_epoch_and_bnf_mode_cannot_leak_across_types(policy):
    service, db, _key = policy
    purchase = replace(
        service.source,
        scope=replace(service.source.scope, provider_id="numilog"),
    )
    loan = replace(
        service.source, kind="loan",
        scope=replace(service.source.scope, provider_id="bnf-pret"),
    )
    numilog_id = bridge._source_id(purchase.scope)
    bnf_id = bridge._source_id(loan.scope)
    db.metadata_set("sync.source.numilog.materialization.consent.v1." + numilog_id,
                    "on_start:v1")
    with db._lock, db._connect() as conn:
        assert bridge.toggle_state(conn, purchase) == "off"  # epoch missing
        assert bridge.toggle_state(conn, loan) is None
    db.metadata_set("sync.source.numilog.materialization.epoch.v1." + numilog_id, "a" * 32)
    with db._lock, db._connect() as conn:
        assert bridge.toggle_state(conn, purchase) == "on"
        assert bridge.toggle_state(conn, loan) is None
    db.metadata_set("sync.source.bnf_auto." + bnf_id, "1")
    with db._lock, db._connect() as conn:
        assert bridge.toggle_state(conn, loan) == "on"
        assert bridge.toggle_state(conn, purchase) == "on"
