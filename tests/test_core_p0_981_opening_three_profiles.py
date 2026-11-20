"""#981: scoped opening observations with three profiles and two Paris sessions.

Real LibraryService and SQLite. Offline producer invocations and phase costs
are not HTTP measurements or a real provider performance benchmark.
"""
from time import monotonic

import pytest

from cifereca.config import Config
from cifereca.core import canonical_sync_stage
from cifereca.core.source_context import source_context_epoch
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan
from cifereca.services import LibraryService
from cifereca.storage import Storage
from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from tests.synthetic_loan_inventory import attach_opening_loan_reader


@pytest.mark.parametrize("fail_second_cycle", [False, True])
def test_opening_three_profiles_two_paris_sources(
    tmp_path, monkeypatch, fail_second_cycle,
):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    owners = ("default", "alex", "third")
    config.set("identities", [
        {"id": owner, "label": owner, "directory_name": owner}
        for owner in owners
    ])
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics())
    try:
        accounts = [
            Account(display_name="Synthetic Paris", unique_id="same-reader",
                    provider_id="paris", credential_id="card-" + owner,
                    identity_id=owner)
            for owner in owners[:2]
        ]
        for account in accounts:
            connection = service.storage.provider_connection_put({
                "identity_id": account.identity_id, "provider_id": "paris",
                "credential_id": account.credential_id, "label": "Synthetic Paris",
            })
            service.storage.provider_account_put({
                "connection_id": connection, "identity_id": account.identity_id,
                "provider_id": "paris", "credential_id": account.credential_id,
                "account_id": account.unique_id,
            })
        service.accounts = accounts
        enable_synthetic_paris_opening(service)
        calls = []
        cycle = 0

        def read(account, *, force=False):
            assert force is True
            assert any(account is registered for registered in accounts)
            scope = (account.identity_id, account.credential_id, account.unique_id)
            calls.append(("loan", scope))
            # Simulate navigation while an independent source observation runs.
            service.select_identity("third" if account is accounts[0] else "alex")
            if cycle == 2 and fail_second_cycle and account is accounts[0]:
                raise RuntimeError("synthetic isolated inventory failure")
            return [Loan(
                id="same-loan", provider="DILICOM", medium="digital",
                record_id="same-edition", title="Synthetic book",
                source_provider="paris", source_credential=account.credential_id,
                source_account_id=account.unique_id, identity_id=account.identity_id,
                due_at="2099-01-01T00:00:00Z",
            )]

        def rights(_service, account, **_kwargs):
            assert _service is service
            calls.append(("resources", (
                account.identity_id, account.credential_id, account.unique_id,
            )))
            return None

        attach_opening_loan_reader(service, read)
        monkeypatch.setattr(canonical_sync_stage, "observe_canonical_rights_in_sync", rights)
        monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                            lambda *a, **kw: pytest.fail("no offered delivery"))
        service.config.set("kobo_backup.enabled", False)
        scopes = [(a.identity_id, a.credential_id, a.unique_id) for a in accounts]
        baseline = source_context_epoch(service)
        run_ids = {}

        for cycle in (1, 2):
            calls.clear()
            started = monotonic()
            result = service.opening_sync()
            elapsed = monotonic() - started
            rows = {row["identity_id"]: row for row in result["identities"]}
            assert set(rows) == set(owners)
            failed = cycle == 2 and fail_second_cycle
            expected = [("loan", scopes[0])]
            if not failed:
                expected.append(("resources", scopes[0]))
            expected.extend([("loan", scopes[1]), ("resources", scopes[1])])
            assert calls == expected
            assert source_context_epoch(service) == baseline
            assert service.current_identity.identity_id == "alex"
            assert [(loan.identity_id, loan.source_credential, loan.id)
                    for loan in service.last_loans] == [
                ("alex", "card-alex", "same-loan"),
            ]
            for owner in owners:
                row = rows[owner]
                latest = service.storage.latest_sync_run(owner)
                assert latest["status"] == row["status"]
                assert latest["sync_run_id"] != run_ids.get(owner)
                run_ids[owner] = latest["sync_run_id"]
                if owner == "third":
                    assert "source_observation_timing" not in row
                    assert row["status"] == "success"
                    continue
                timing = row["source_observation_timing"]
                assert latest["detail"]["source_observation_timing"] == timing
                assert set(timing) == (
                    {"loan_inventory"} if owner == "default" and failed
                    else {"loan_inventory", "canonical_resources"}
                )
                assert all(value["attempts"] == 1
                           and type(value["elapsed_ms"]) is int
                           and 0 <= value["elapsed_ms"] <= elapsed * 1000 + 1000
                           for value in timing.values())
                if owner == "default" and failed:
                    assert row["status"] == "failed"
                else:
                    assert row["status"] == "success"
            # An unsuccessful second read proves no termination of the
            # first source's previously observed right.
            assert {(r["identity_id"], r["credential_id"], r["loan_id"])
                    for r in service.storage.entitlements(provider_id="paris")} == {
                ("default", "card-default", "same-loan"),
                ("alex", "card-alex", "same-loan"),
            }
            assert service.storage.acquisitions() == service.storage.placements() == []
    finally:
        service.close()
