"""#4859: canonical-neutral, append-only Kobo W4 file evidence.

SQLite/EPUB synthetic. Never grants provider rights, a delivered edition,
OriginLink, Placement or an acquisition checkpoint.
"""
from __future__ import annotations

import json
import os

import pytest

from cifereca.core import kobo_local_reconciliation as repair
from tests.test_core_1345_confirmed_local_reconciliation import case  # noqa: F401


@pytest.fixture
def observation(case):
    with case.storage._lock, case.storage._connect() as db:
        connection_id = db.execute(
            "SELECT connection_id FROM provider_connections "
            "WHERE provider_id='kobo' AND credential_id='vault-1'"
        ).fetchone()[0]
    case.storage.provider_account_put({
        "connection_id": connection_id,
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "default", "account_id": "PRIVATE-account",
    })
    opts = {
        **case.options, "identity_id": "default",
        "expected_size": case.target.stat().st_size,
    }
    return case, opts


def _snapshot(case):
    with case.storage._lock, case.storage._connect() as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "backup_items", "entitlements", "acquisition_ledger",
                "source_references", "origin_links", "artifacts",
                "placements", "catalog_entries", "provider_accounts",
            )
        }


def _observe(case, options, **kwargs):
    return repair.observe_kobo_local_file_only(
        case.service, **{**options, **kwargs}
    )


def test_file_only_preview_and_confirm_never_publish_legacy_or_canonical_right(
    observation,
):
    case, args = observation
    before = _snapshot(case)
    preview = _observe(case, args)
    assert preview["status"] == "preview"
    assert preview["requires_provider_attestation"] is True
    assert preview["rights_attested"] is False
    assert preview["canonical_delivery_attested"] is False
    assert _snapshot(case) == before

    confirmed = _observe(case, args, confirmation=preview["plan_token"])
    assert confirmed["status"] == "observed"
    assert confirmed["changed"] == 1
    assert confirmed["rights_mutations"] == 0
    assert confirmed["legacy_backup_mutations"] == 0
    assert confirmed["file_mutations"] == 0
    assert _snapshot(case) == before
    assert _observe(case, args)["status"] == "already_observed"
    assert _observe(case, args, confirmation=preview["plan_token"])["status"] == "already_observed"

    with case.storage._lock, case.storage._connect() as db:
        evidence = db.execute(
            "SELECT value FROM metadata WHERE key LIKE "
            "'core.kobo.local-file-only.%'"
        ).fetchall()
    assert len(evidence) == 1
    payload = json.loads(evidence[0][0])
    assert payload["authority"] == "historical_local_bytes_only"
    assert payload["rights_attested"] is False
    assert payload["canonical_delivery_attested"] is False
    assert payload["file"]["sha256"] == case.sha
    assert payload["claimed_scope"]["account_id"] == "PRIVATE-account"
    public = json.dumps(confirmed) + json.dumps(preview)
    for private in ("PRIVATE", "vault-1", "default", case.sha, str(case.target)):
        assert private not in public


def test_no_legacy_backup_required_for_file_only_observation(observation):
    case, args = observation
    with case.storage._lock, case.storage._connect() as db:
        db.execute("DELETE FROM backup_items WHERE provider_id='kobo'")
    before = _snapshot(case)
    preview = _observe(case, args)
    assert preview["status"] == "preview"
    assert _observe(case, args, confirmation=preview["plan_token"])["status"] == "observed"
    assert _snapshot(case) == before


@pytest.mark.parametrize("mutation", ("disabled", "wrong_account", "wrong_owner", "no_borrower"))
def test_file_only_scope_is_exact_before_any_private_file_hash(
    observation, monkeypatch, mutation,
):
    case, args = observation
    if mutation == "disabled":
        case.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "vault-1", "enabled": False,
        })
    elif mutation == "wrong_account":
        args["account_id"] = "another-borrower"
    elif mutation == "wrong_owner":
        args["identity_id"] = "other"
    else:
        with case.storage._lock, case.storage._connect() as db:
            db.execute("DELETE FROM provider_accounts WHERE provider_id='kobo'")
    monkeypatch.setattr(
        repair, "_read_hash_within",
        lambda *_a: pytest.fail("unbound source may not hash a file"),
    )
    before = _snapshot(case)
    with pytest.raises(
        repair.ReconciliationRejected,
        match="source_scope_unavailable|borrower_scope_unavailable",
    ):
        _observe(case, args)
    assert _snapshot(case) == before


def test_preview_token_invalidated_by_same_byte_new_inode(observation):
    case, args = observation
    preview = _observe(case, args)
    replacement = case.target.with_name("replacement.epub")
    replacement.write_bytes(case.target.read_bytes())
    os.replace(replacement, case.target)
    before = _snapshot(case)
    with pytest.raises(repair.ReconciliationRejected, match="preview_changed"):
        _observe(case, args, confirmation=preview["plan_token"])
    assert _snapshot(case) == before


def test_confirm_does_not_write_if_local_bytes_changed(observation):
    case, args = observation
    preview = _observe(case, args)
    case.target.write_bytes(case.target.read_bytes() + b"mutation")
    before = _snapshot(case)
    with pytest.raises(repair.ReconciliationRejected, match="file_hash_mismatch"):
        _observe(case, args, confirmation=preview["plan_token"])
    assert _snapshot(case) == before


def test_file_only_journal_is_immutable_after_replacement(observation):
    case, args = observation
    preview = _observe(case, args)
    assert _observe(case, args, confirmation=preview["plan_token"])["status"] == "observed"
    replacement = case.target.with_name("fresh-epub.epub")
    replacement.write_bytes(case.target.read_bytes())
    os.replace(replacement, case.target)
    before = _snapshot(case)
    with pytest.raises(repair.ReconciliationRejected, match="file_evidence_conflict"):
        _observe(case, args)
    assert _snapshot(case) == before



def test_offline_existing_profile_exposes_file_only_route_without_legacy_mutation(
    observation,
):
    case, args = observation
    before = _snapshot(case)
    run = lambda **extra: repair.reconcile_existing_profile(
        config_path=case.service.config.path,
        database_path=case.storage.path,
        file_only=True, **{**args, **extra},
    )
    preview = run()
    assert preview["status"] == "preview"
    assert _snapshot(case) == before
    assert run(confirmation=preview["plan_token"])["status"] == "observed"
    assert _snapshot(case) == before
    assert run()["status"] == "already_observed"


@pytest.mark.parametrize("value", (None, "true", 1, []))
def test_offline_entry_rejects_ambiguous_file_only_dispatch(observation, value):
    case, args = observation
    if value is None:
        # Explicit None is not equivalent to a requested read-only observation.
        pass
    with pytest.raises(repair.ReconciliationRejected, match="invalid_request"):
        repair.reconcile_existing_profile(
            config_path=case.service.config.path,
            database_path=case.storage.path,
            file_only=value, **args,
        )
