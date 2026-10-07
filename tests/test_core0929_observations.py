from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(tmp_path: Path) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("providers.accounts", [])
    config.set("identities", [{"id": "alice", "label": "Alice", "directory_name": "alice"}])
    return LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))


def _account(provider: str, credential: str, unique_id: str):
    return SimpleNamespace(
        identity_id="alice",
        provider_id=provider,
        credential_id=credential,
        unique_id=unique_id,
    )


def _loan(loan_id: str):
    return SimpleNamespace(id=loan_id)


def test_multi_account_observations_are_merged_without_losing_previous_account(tmp_path: Path):
    service = _service(tmp_path)
    first = _account("paris", "card-a", "account-a")
    second = _account("kobo", "vault-b", "account-b")
    try:
        service._record_loan_statistics(first, [_loan("loan-a")])
        service._record_loan_statistics(second, [_loan("loan-b")])

        raw = json.loads(service.storage.metadata_get("loan_statistics:alice"))
        assert len(raw) == 2
        loan_sets = {tuple(value["loan_keys"]) for value in raw.values()}
        assert len(loan_sets) == 2
        assert all(value["origin"] == "provider" for value in raw.values())
        assert all(value["observed_at"] is not None for value in raw.values())
    finally:
        service.close()


def test_cached_observation_never_overwrites_fresher_provider_observation(tmp_path: Path):
    service = _service(tmp_path)
    account = _account("paris", "card-a", "account-a")
    try:
        service._record_loan_statistics(account, [_loan("fresh")], from_cache=False)
        before = service.storage.metadata_get("loan_statistics:alice")

        service._record_loan_statistics(account, [_loan("stale")], from_cache=True)
        after = service.storage.metadata_get("loan_statistics:alice")

        assert after == before
        value = next(iter(json.loads(after).values()))
        assert value["origin"] == "provider"
        assert value["observed_at"] is not None
    finally:
        service.close()


def test_cache_observation_can_seed_missing_account_without_replacing_others(tmp_path: Path):
    service = _service(tmp_path)
    live = _account("paris", "card-a", "account-a")
    cached = _account("kobo", "vault-b", "account-b")
    try:
        service._record_loan_statistics(live, [_loan("live")], from_cache=False)
        service._record_loan_statistics(cached, [_loan("cached")], from_cache=True)

        values = list(json.loads(service.storage.metadata_get("loan_statistics:alice")).values())
        assert len(values) == 2
        origins = sorted(value["origin"] for value in values)
        assert origins == ["cache", "provider"]
        cached_value = next(value for value in values if value["origin"] == "cache")
        assert cached_value["observed_at"] is None
    finally:
        service.close()


def test_identity_statistics_reads_persisted_observations_without_network(tmp_path: Path):
    service = _service(tmp_path)
    account = _account("paris", "card-a", "account-a")
    service.accounts_for_identity = lambda identity_id: [account]
    service.search = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("statistics must not search"))
    service.loans = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("statistics must not refresh loans"))
    try:
        service._record_loan_statistics(account, [_loan("loan-a")])
        stats = service.identity_statistics("alice")

        assert stats["identity_id"] == "alice"
        assert stats["known_loans"] == 1
    finally:
        service.close()


def test_atomic_metadata_update_recovers_from_legacy_non_dict_payload(tmp_path: Path):
    storage = Storage(tmp_path / "db.sqlite3")
    try:
        storage.metadata_set("loan_statistics:alice", "[]")
        storage.metadata_json_put_item("loan_statistics:alice", "account", {"origin": "provider"})

        value = json.loads(storage.metadata_get("loan_statistics:alice"))
        assert value == {"account": {"origin": "provider"}}
    finally:
        storage.close()
