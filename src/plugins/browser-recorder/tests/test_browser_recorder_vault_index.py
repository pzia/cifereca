"""Internal storage integrity and replacement quota, with fictional keys."""
from __future__ import annotations

import json
import pytest
pytest.importorskip("cryptography")

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from capture_engine_fixture import FixtureVault, VaultError

PRIMARY = "FICTITIOUS-INDEX-SECRET-2026"
OTHER = "FICTITIOUS-ANOTHER-SECRET-2026"
ORIGIN = "https://fictitious-catalogue.example.org"
ACCOUNT = "acct-FAKE-PRIVATE-99"


def _record(vault, password, state, number):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/books?account=" + ACCOUNT,
        status=200, mime="application/json",
        response_body=json.dumps({"ProductId": number, "AccountId": ACCOUNT}),
        response_body_state="captured",
    ))
    writer = vault.begin(password)
    writer.save(trace, state, force=True)
    trace.destroy()
    return writer.session_id


def test_corrupt_capture_is_preserved_and_other_capture_can_be_read(tmp_path):
    vault = FixtureVault(tmp_path / "vault")
    good = _record(vault, PRIMARY, "completed", 10)
    corrupt = _record(vault, PRIMARY, "completed", 11)
    path = vault.path / (corrupt + ".cbrv")
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes((original[-1] ^ 0xFF,)))
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.open(corrupt, PRIMARY)
    one = vault.open(good, PRIMARY)
    try:
        assert one.private_summary()["origin"] == ORIGIN
        assert ACCOUNT not in repr(one.private_summary())
    finally:
        one.trace.destroy()
    assert set(vault.list_ids()) == {good, corrupt}
    assert original != path.read_bytes()


def test_snapshot_replacement_counts_only_final_encrypted_bytes(tmp_path, monkeypatch):
    import cifereca.core.capture_vault as vault_api

    vault = FixtureVault(tmp_path / "vault")
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", ORIGIN + "/catalog", status=200,
    ))
    writer = vault.begin(PRIMARY)
    writer.save(trace, "recording", force=True)
    first = vault.path / (writer.session_id + ".cbrv")
    previous_size = first.stat().st_size
    monkeypatch.setattr(vault_api, "MAX_VAULT_BYTES", previous_size + 256)
    # Every incremental/final replacement is safe: it occupies one file.
    writer.save(trace, "recording", force=True)
    writer.save(trace, "completed", force=True)
    assert vault.list_ids() == (writer.session_id,)
    assert first.stat().st_size <= previous_size + 256
    assert vault.open(writer.session_id, PRIMARY).recovery_status == "completed"
    # But a second distinct capture may NOT bypass the same global cap.
    another = vault.begin(PRIMARY)
    with pytest.raises(VaultError, match="vault_limit"):
        another.save(trace, "completed", force=True)
    assert vault.list_ids() == (writer.session_id,)
    assert not list(vault.path.glob("*.part"))
    trace.destroy()
