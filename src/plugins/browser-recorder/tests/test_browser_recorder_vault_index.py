"""Sensitive session metadata are visible only after explicit key unlock."""
from __future__ import annotations

import json
import pytest
pytest.importorskip("cryptography")

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import PrivateSessionVault, VaultError

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


def test_private_index_requires_unlock_and_isolates_other_keys(tmp_path):
    vault = PrivateSessionVault(tmp_path / "vault")
    assert vault.list_ids() == ()
    assert not vault.path.exists()
    one = _record(vault, PRIMARY, "completed", 1)
    two = _record(vault, PRIMARY, "recording", 2)
    three = _record(vault, OTHER, "completed", 3)
    assert set(vault.list_ids()) == {one, two, three}
    assert not any(ACCOUNT in filename for filename in vault.list_ids())
    with pytest.raises(VaultError, match="vault_passphrase_required"):
        vault.unlocked_index("too-short")
    overview, unavailable = vault.unlocked_index(PRIMARY)
    assert unavailable == 1
    assert {row["id"] for row in overview} == {one, two}
    assert {row["status"] for row in overview} == {"completed", "partial"}
    assert all(row["origin"] == ORIGIN for row in overview)
    assert all(row["exchanges"] == 1 for row in overview)
    assert all(row["saved_at"] for row in overview)
    assert ACCOUNT not in repr(overview)  # no query/body reaches the index
    wrong, unavailable = vault.unlocked_index("FICTIONAL-TOTALLY-WRONG-2026")
    assert not wrong and unavailable == 3
    only_third, unavailable = vault.unlocked_index(OTHER)
    assert len(only_third) == 1 and only_third[0]["id"] == three
    assert unavailable == 2
    assert not list(tmp_path.rglob("*.json"))


def test_invalid_session_does_not_destroy_valid_index_rows(tmp_path):
    vault = PrivateSessionVault(tmp_path / "vault")
    good = _record(vault, PRIMARY, "completed", 10)
    corrupt = _record(vault, PRIMARY, "completed", 11)
    path = vault.path / (corrupt + ".cbrv")
    payload = path.read_bytes()
    path.write_bytes(payload[:-1] + bytes((payload[-1] ^ 0xFF,)))
    report, unavailable = vault.unlocked_index(PRIMARY)
    assert unavailable == 1
    assert len(report) == 1 and report[0]["id"] == good
    assert payload != path.read_bytes()
    assert set(vault.list_ids()) == {good, corrupt}
