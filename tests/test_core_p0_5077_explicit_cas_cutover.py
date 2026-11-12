"""#5077: explicit CAS revocation wins over a still-ON legacy source switch.

This verifies the cutover safety boundary; it deliberately does not
automatically migrate a historical ON into CAS ON.
"""
from cifereca.core import source_auto_consent_bridge as bridge
from cifereca.core.source_sync_consent import read_policy
from cifereca.core.source_acquisition_consent import consent_current_in_transaction
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3719_source_sync_consent import source, change


def test_explicit_cas_off_revokes_legacy_on_without_deleting_legacy_row(
    source, monkeypatch,
):
    service, db, _root, calls, broker = source
    key = service.source
    prefix = "sync.test.cutover.legacy."
    revision_prefix = "sync.test.cutover.revision."
    monkeypatch.setitem(
        bridge._POLICIES, (key.scope.provider_id, key.kind),
        (prefix, "legacy-on", revision_prefix),
    )
    suffix = bridge._source_id(key.scope)
    db.metadata_set(prefix + suffix, "legacy-on")
    db.metadata_set(revision_prefix + suffix, "a" * 32)
    with db._lock, db._connect() as conn:
        assert bridge.toggle_state(conn, key) == "on"
        assert consent_current_in_transaction(conn, key)

    # The existing, explicitly invoked CAS writer is the only cutover path.
    # This negative choice never inherits ON from the historical metadata.
    off = change(source, False)
    assert off["configured"] and not off["enabled"]
    assert read_policy(db, key.scope).mode == "off"
    with db._lock, db._connect() as conn:
        assert bridge.toggle_state(conn, key) == "off"
        assert not consent_current_in_transaction(conn, key)
    assert db.metadata_get(prefix + suffix) == "legacy-on"
    assert db.metadata_get(revision_prefix + suffix) == "a" * 32
    assert calls == []


def test_explicit_cas_aba_never_restores_old_epoch(source, monkeypatch):
    service, db, _root, calls, broker = source
    key = service.source
    prefix = "sync.test.cutover.legacy."
    monkeypatch.setitem(
        bridge._POLICIES, (key.scope.provider_id, key.kind),
        (prefix, "legacy-on", None),
    )
    suffix = bridge._source_id(key.scope)
    db.metadata_set(prefix + suffix, "legacy-on")
    # Legacy unversioned ON is not promoted to a CAS revision.
    assert read_policy(db, key.scope).mode == "unset"
    on = change(source, True)
    off = change(source, False, on["revision"])
    new_on = change(source, True, off["revision"])
    assert on["revision"] != new_on["revision"]
    assert read_policy(db, key.scope).revision == new_on["revision"]
    with db._lock, db._connect() as conn:
        assert bridge.consent_generation_in_transaction(conn, key) == (
            "source:" + new_on["revision"]
        )
    assert db.metadata_get(prefix + suffix) == "legacy-on"
    assert calls == []
