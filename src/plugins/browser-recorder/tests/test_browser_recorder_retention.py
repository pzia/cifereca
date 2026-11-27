"""Automatic retention: authenticated preflight and age/volume bounds.

Fixtures are entirely fictional; safe through independently encrypted storage.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import pytest
pytest.importorskip("cryptography")

import browser_recorder_vault as v
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from capture_engine_fixture import FixtureVault, VaultError

PASSWORD = "FICTIONAL-SESSION-RETENTION-KEY-2026"
OTHER = "SEPARATE-FICTIONAL-RETENTION-KEY-2026"
ORIGIN = "https://fictional-books.example.org"
FAKE = "FICTIONAL-ACCOUNT-PRIVATE-99"


def session(vault, tmp_path, monkeypatch, *, age_days, state="completed",
            key=PASSWORD, label=None):
    old = datetime.now(timezone.utc) - timedelta(days=age_days)
    actual_date = v.datetime
    class HistoricalDate(actual_date):
        @classmethod
        def now(cls, tz=None):
            return old if tz is not None else old.replace(tzinfo=None)
    writer = vault.begin(key)
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", ORIGIN + "/catalog?account=" + FAKE,
        status=200, mime="application/json",
        response_body='{"ProductId":214,"account":"'+FAKE+'"}',
        response_body_state="captured",
    ))
    with monkeypatch.context() as m:
        m.setattr(v, "datetime", HistoricalDate)
        writer.save(trace, state, force=True)
    trace.destroy()
    if label:
        vault.set_label(writer.session_id, key, label)
    return writer.session_id


def capture_bytes(vault):
    return {x.name: x.read_bytes() for x in vault.path.iterdir()
            if x.suffix in {".cbrv", ".cbrn"}}


def prune(vault, *, days=90, keep="", **kwargs):
    return vault.auto_prune(PASSWORD, older_than_days=days,
                            pinned=lambda label, now: bool(keep and label == keep),
                            **kwargs)


def test_age_retention_and_pin_preserve_young_and_pinned(tmp_path, monkeypatch):
    vault = FixtureVault(tmp_path / "vault")
    old = session(vault, tmp_path, monkeypatch, age_days=130, label="Old private")
    young = session(vault, tmp_path, monkeypatch, age_days=10)
    partial = session(vault, tmp_path, monkeypatch, age_days=190, state="recording")
    interrupted = session(vault, tmp_path, monkeypatch, age_days=200, state="interrupted")
    pinned = session(vault, tmp_path, monkeypatch, age_days=220, label="pin")
    before = capture_bytes(vault)
    with pytest.raises(VaultError, match="vault_retention_invalid"):
        prune(vault, days=0)
    assert capture_bytes(vault) == before
    count, reclaimed = prune(vault, keep="pin")
    assert count == 3 and reclaimed > 0
    assert set(vault.list_ids()) == {young, pinned}
    for name, data in capture_bytes(vault).items():
        assert before[name] == data
    assert not (vault.path / (old + ".cbrn")).exists()
    assert partial not in vault.list_ids() and interrupted not in vault.list_ids()


def test_wrong_key_blocks_entire_purge(tmp_path, monkeypatch):
    vault = FixtureVault(tmp_path / "vault")
    first = session(vault, tmp_path, monkeypatch, age_days=130)
    second = session(vault, tmp_path, monkeypatch, age_days=160, key=OTHER)
    before = capture_bytes(vault)
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        prune(vault)
    assert capture_bytes(vault) == before and set(vault.list_ids()) == {first, second}


def test_symlink_or_hardlinked_label_blocks_all_deletion(tmp_path, monkeypatch):
    vault = FixtureVault(tmp_path / "vault")
    first = session(vault, tmp_path, monkeypatch, age_days=130, label="FICTIONAL")
    second = session(vault, tmp_path, monkeypatch, age_days=160)
    victim = vault.path / (first + ".cbrn")
    victim.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"OUTSIDE")
    victim.symlink_to(outside)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        prune(vault)
    assert set(vault.list_ids()) == {first, second}
    assert outside.read_bytes() == b"OUTSIDE"
    victim.unlink()
    vault.set_label(first, PASSWORD, "new label")
    os.link(victim, outside.with_name("hardlink"))
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        prune(vault)
    assert set(vault.list_ids()) == {first, second}


def test_recent_orphan_is_not_pruned_by_age_retention(tmp_path, monkeypatch):
    vault = FixtureVault(tmp_path / "vault")
    sid = session(vault, tmp_path, monkeypatch, age_days=200)
    ciphertext = (vault.path / (sid + ".cbrv")).read_bytes()
    staging = vault.path / ("." + sid + "-feedfeedfeedfeed.part")
    staging.write_bytes(ciphertext)
    staging.chmod(0o600)
    assert prune(vault)[0] == 1
    assert not vault.list_ids()
    assert staging.read_bytes() == ciphertext


@pytest.mark.parametrize("attack", ["tamper", "swap"])
def test_corrupt_or_swapped_label_blocks_all_deletion(tmp_path, monkeypatch, attack):
    vault = FixtureVault(tmp_path / "vault")
    first = session(vault, tmp_path, monkeypatch, age_days=170, label="FIRST")
    second = session(vault, tmp_path, monkeypatch, age_days=175, label="SECOND")
    path = vault.path / (first + ".cbrn")
    original = path.read_bytes()
    if attack == "tamper":
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    else:
        path.write_bytes((vault.path / (second + ".cbrn")).read_bytes())
    before = capture_bytes(vault)
    with pytest.raises(VaultError, match="vault_label_invalid"):
        prune(vault)
    assert capture_bytes(vault) == before
    assert set(vault.list_ids()) == {first, second}


def test_pinned_or_partial_volume_refusal_does_not_delete_other_sessions(tmp_path, monkeypatch):
    import cifereca.core.capture_vault as engine
    vault = FixtureVault(tmp_path / "vault")
    partial = session(vault, tmp_path, monkeypatch, age_days=0, state="recording")
    pinned = session(vault, tmp_path, monkeypatch, age_days=0, label="pin")
    young = session(vault, tmp_path, monkeypatch, age_days=0)
    before = capture_bytes(vault)
    volume = sum(len(x) for x in before.values())
    monkeypatch.setattr(engine, "MAX_VAULT_BYTES", volume)
    with pytest.raises(VaultError, match="vault_limit"):
        prune(vault, keep="pin", reserve_bytes=volume, reserve_session=True)
    assert capture_bytes(vault) == before
    assert set(vault.list_ids()) == {partial, pinned, young}
