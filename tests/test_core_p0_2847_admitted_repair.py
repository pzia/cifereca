"""#5833/#5851: confirmed repair work is Core-owned, with real SQLite/FS."""
from types import SimpleNamespace as NS

import pytest

from tests.test_core_p0_2847_operator_session import _session
from tests.test_core_p0_2847_relocation_transaction import catalogue, rig


def scoped(rig):
    service, operator = _session(rig)
    service.current_identity.directory_name = "alice"
    service.identities = [service.current_identity, NS(identity_id="bob", directory_name="bob")]
    service.accounts = []
    service.registry = object()
    service._source_context_epoch = 1
    service.config.data = {}
    service.config.identities = lambda: [
        {"id": "alice", "directory_name": "alice"},
        {"id": "bob", "directory_name": "bob"},
    ]
    return service, operator


@pytest.mark.parametrize("action,success", [
    ("retirement", "retired"),
    ("linked_catalogue_retirement", "retired"),
    ("relocation", "relocated"),
    ("same_path_restoration", "restored"),
    ("linked_same_path_restoration", "restored"),
])
@pytest.mark.parametrize("change", ["navigation", "source_epoch", "sqlite", "root"])
def test_consumed_confirmation_survives_only_navigation(rig, action, success, change):
    db, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = scoped(rig)
    if "linked" in action:
        catalogue(rig)
    if "restoration" in action:
        old.write_bytes(payload)
    kwargs = {"placement_id": pid}
    if action == "relocation":
        kwargs["candidate_path"] = new
    preview = getattr(operator, "preview_" + action)(**kwargs)
    assert preview["status"] in ("ready", "review_required")
    before = db.placement_get(pid)
    catalog_before = db.catalog_entries()
    command = operator.admit_confirmation(user_confirmed=True)
    assert callable(command)
    assert operator.admit_confirmation(user_confirmed=True) is None
    owner = service.current_identity
    service.current_identity = service.identities[1]
    if change == "navigation":
        service.current_identity = owner
    elif change == "source_epoch":
        service._source_context_epoch += 1
    elif change == "sqlite":
        db.metadata_set("synthetic.unrelated", "changed")
    elif change == "root":
        service.config.root = str(root / "rebound")
    operator.close()
    outcome = command()
    if change == "navigation":
        assert outcome["status"] == success
    else:
        assert outcome == {"status": "blocked", "reason": "scope_changed", "read_only": True}
        assert db.placement_get(pid) == before
        assert db.catalog_entries() == catalog_before
    assert command()["reason"] == "confirmation_required"
    assert new.read_bytes() == payload
    if "restoration" in action:
        assert old.read_bytes() == payload
    else:
        assert not old.exists()
    assert db.entitlements() == []
    assert db.acquisitions() == []
    with db._lock, db._connect() as sql:
        assert sql.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 0


def test_source_revocation_inside_writer_rolls_back(rig, monkeypatch):
    from cifereca.core import missing_placement_operator as module
    db, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = scoped(rig)
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    command = operator.admit_confirmation(user_confirmed=True)
    original = module._AdmittedMissingRepair.in_transaction

    def revoke_before_commit(self, sql, *, initial):
        if not initial:
            service._source_context_epoch += 1
        return original(self, sql, initial=initial)

    monkeypatch.setattr(module._AdmittedMissingRepair, "in_transaction", revoke_before_commit)
    # The bound method is already captured at admission; re-admit after patch.
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    command = operator.admit_confirmation(user_confirmed=True)
    assert command()["reason"] == "scope_changed"
    assert db.placement_get(pid)["state"] == "missing"
    assert new.read_bytes() == payload
