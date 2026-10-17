"""A local preview cannot retain proof across a source/directory change."""
from dataclasses import replace
import json

import pytest

from cifereca.core import numilog_local_preview
from tests.test_core_p0_1905_numilog_offline_preview import (
    _broker, _ledger, _owned, _preview,
)
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize("change", [
    "rebind", "disable", "account", "account_in_place", "directory", "collision",
])
@pytest.mark.parametrize("boundary", ["storage", "hash"])
@pytest.mark.parametrize("number", [1, 2])
def test_scope_changes_discard_preview_and_stop_subsequent_file_reads(
    tmp_path, monkeypatch, change, boundary, number,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        for index, variant in enumerate(("a" * 16, "b" * 16)[:number]):
            item, eid = _owned(service, account, str(index), variant=variant)
            # Independent purchases with different bytes and placements.
            path = _ledger(service, account, item, eid, data=f"book-{index}".encode())
            placement = service.storage.placement_for_path(path)
            renamed = path.with_name(f"book-{index}.epub")
            path.rename(renamed)
            service.storage.placement_delete(placement["placement_id"])
            service.storage.placement_put({
                **placement, "placement_id": None, "path": str(renamed),
            })
        broker, sid = _broker(service, account)
        session.library = lambda: pytest.fail("preview must remain offline")
        hashed = []

        def mutate():
            if change == "rebind":
                service.storage.provider_connection_put({
                    "provider_id": "numilog", "credential_id": account.credential_id,
                    "identity_id": "alex", "enabled": True,
                })
            elif change == "disable":
                service.storage.metadata_set("sync.source.enabled." + sid, "off")
            elif change == "account":
                service.accounts[0] = replace(account, unique_id="another-account")
            elif change == "account_in_place":
                account.unique_id = "another-account"
            elif change == "directory":
                service.config.set("library.directory", str(tmp_path / "another-library"))
            else:
                service.identities[1] = replace(
                    service.identities[1], directory_name="hugues",
                )

        real_hash = numilog_local_preview._read_hash_within

        def hash_then_change(path, roots):
            hashed.append(path)
            result = real_hash(path, roots)
            if boundary == "hash" and len(hashed) == 1:
                mutate()
            return result

        monkeypatch.setattr(numilog_local_preview, "_read_hash_within", hash_then_change)
        if boundary == "storage":
            real_evidence = service.storage.source_materialization_evidence

            def evidence_then_change(**scope):
                rows = real_evidence(**scope)
                mutate()
                return rows

            monkeypatch.setattr(service.storage, "source_materialization_evidence", evidence_then_change)
        outcome = _preview(broker, sid, verify=True)
        assert not outcome.ok
        assert outcome.changed == 0
        assert outcome.facts["status"] == "source_changed"
        assert outcome.facts["acquisition_authorized"] is False
        assert "counts" not in outcome.facts
        assert len(hashed) == (0 if boundary == "storage" else 1)
        assert calls == []
        assert "another-library" not in json.dumps(outcome.facts)
    finally:
        service.close()


@pytest.mark.parametrize("verify", [False, True])
def test_directory_change_invalidates_even_an_empty_preview(tmp_path, monkeypatch, verify):
    service = _service(tmp_path)
    try:
        account, _, calls = _connect(service, number=0)
        broker, sid = _broker(service, account)
        real_evidence = service.storage.source_materialization_evidence

        def evidence_then_change_root(**scope):
            rows = real_evidence(**scope)
            service.config.set("library.directory", str(tmp_path / "new-root"))
            return rows

        monkeypatch.setattr(service.storage, "source_materialization_evidence", evidence_then_change_root)
        result = _preview(broker, sid, verify=verify)
        assert not result.ok
        assert result.facts["status"] == "source_changed"
        assert "counts" not in result.facts
        assert not (tmp_path / "new-root").exists()
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("verify", [False, True])
def test_replacement_account_cannot_validate_a_whitespace_distinct_scope(
    tmp_path, monkeypatch, verify,
):
    service = _service(tmp_path)
    try:
        account, _, calls = _connect(service, number=0)
        item, eid = _owned(service, account)
        _ledger(service, account, item, eid)
        broker, sid = _broker(service, account)
        real_evidence = service.storage.source_materialization_evidence

        def evidence_then_replace(**scope):
            rows = real_evidence(**scope)
            # The broker still holds the old mutable object, while the
            # live account now has a whitespace-distinct account ID. Source
            # IDs strip whitespace, so their hash alone is insufficient.
            service.accounts[0] = replace(account, unique_id=" account ")
            return rows

        monkeypatch.setattr(service.storage, "source_materialization_evidence", evidence_then_replace)
        result = _preview(broker, sid, verify=verify)
        assert not result.ok
        assert result.facts["status"] == "source_changed"
        assert "counts" not in result.facts
        assert calls == []
    finally:
        service.close()
