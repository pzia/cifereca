"""Explicit retention: no automatic or non-authenticated session deletion.

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
from browser_recorder_vault import PrivateSessionVault, VaultError

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


def test_preview_does_nothing_and_requires_fresh_consent(tmp_path, monkeypatch):
    vault = PrivateSessionVault(tmp_path / "vault")
    old = session(vault, tmp_path, monkeypatch, age_days=130,
                  label="Ancienne fiction privée")
    young = session(vault, tmp_path, monkeypatch, age_days=10)
    partial = session(vault, tmp_path, monkeypatch, age_days=190,
                      state="recording")
    interrupted = session(vault, tmp_path, monkeypatch, age_days=200,
                          state="interrupted")
    other = session(vault, tmp_path, monkeypatch, age_days=220, key=OTHER)
    before = capture_bytes(vault)
    assert set(vault.list_ids()) == {old, young, partial, interrupted, other}
    with pytest.raises(VaultError, match="vault_retention_invalid"):
        vault.retention_preview(PASSWORD, 0)
    with pytest.raises(VaultError, match="vault_retention_invalid"):
        vault.retention_preview(PASSWORD, 1)
    with pytest.raises(VaultError, match="vault_passphrase_required"):
        vault.retention_preview("bad", 90)
    preview = vault.retention_preview(PASSWORD, 90)
    assert preview.count == 1
    assert preview.fingerprints[0][0] == old
    assert preview.total_bytes > 0
    assert FAKE not in repr(preview)
    assert capture_bytes(vault) == before
    with pytest.raises(VaultError, match="vault_confirmation_required"):
        vault.retention_apply(preview, PASSWORD)
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.retention_apply(preview, OTHER, confirmed=True)
    assert capture_bytes(vault) == before
    count, freed = vault.retention_apply(preview, PASSWORD, confirmed=True)
    assert count == 1 and freed == preview.total_bytes
    assert old not in vault.list_ids()
    assert set(vault.list_ids()) == {young, partial, interrupted, other}
    assert (vault.path / (old + ".cbrn")).exists() is False
    after = capture_bytes(vault)
    for name, data in before.items():
        if name.startswith(old):
            assert name not in after
        else:
            assert after[name] == data


def test_replaced_source_or_label_aborts_whole_batch(tmp_path, monkeypatch):
    vault = PrivateSessionVault(tmp_path / "vault")
    one = session(vault, tmp_path, monkeypatch, age_days=150, label="old title")
    two = session(vault, tmp_path, monkeypatch, age_days=200)
    preview = vault.retention_preview(PASSWORD, 90)
    assert preview.count == 2
    vault.set_label(one, PASSWORD, "new title")
    before = capture_bytes(vault)
    with pytest.raises(VaultError, match="vault_retention_changed"):
        vault.retention_apply(preview, PASSWORD, confirmed=True)
    assert capture_bytes(vault) == before
    assert set(vault.list_ids()) == {one, two}


def test_expired_preview_and_no_empty_bulk_action(tmp_path, monkeypatch):
    vault = PrivateSessionVault(tmp_path / "vault")
    session(vault, tmp_path, monkeypatch, age_days=120)
    empty = vault.retention_preview(PASSWORD, 180)
    assert empty.count == 0
    with pytest.raises(VaultError, match="vault_retention_invalid"):
        vault.retention_apply(empty, PASSWORD, confirmed=True)
    preview = vault.retention_preview(PASSWORD, 90)
    assert preview.count == 1
    expired = v.RetentionPlan(
        preview.older_than_days,
        (datetime.now(timezone.utc) - timedelta(days=91)).isoformat(),
        preview.fingerprints, preview.total_bytes,
    )
    with pytest.raises(VaultError, match="vault_retention_expired"):
        vault.retention_apply(expired, PASSWORD, confirmed=True)
    assert len(vault.list_ids()) == 1


def test_symlink_or_hardlinked_label_blocks_all_deletion(tmp_path, monkeypatch):
    vault = PrivateSessionVault(tmp_path / "vault")
    first = session(vault, tmp_path, monkeypatch, age_days=130,
                    label="FICTIONAL LABEL")
    second = session(vault, tmp_path, monkeypatch, age_days=160)
    plan = vault.retention_preview(PASSWORD, 90)
    victim = vault.path / (first + ".cbrn")
    victim.unlink()
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"FICTIONAL_NOT_OURS")
    victim.symlink_to(foreign)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        vault.retention_apply(plan, PASSWORD, confirmed=True)
    assert set(vault.list_ids()) == {first, second}
    assert foreign.read_bytes() == b"FICTIONAL_NOT_OURS"
    victim.unlink()
    vault.set_label(first, PASSWORD, "new label")
    other = tmp_path / "hardlink"
    os.link(vault.path / (first + ".cbrn"), other)
    plan2 = vault.retention_preview(PASSWORD, 90) if False else None
    # Hardlinks are rejected even during authenticated preview.
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        vault.retention_preview(PASSWORD, 90)
    other.unlink()
    assert set(vault.list_ids()) == {first, second}


def test_unrelated_orphan_chiffre_is_never_pruned_by_retention(tmp_path, monkeypatch):
    vault = PrivateSessionVault(tmp_path / "vault")
    sid = session(vault, tmp_path, monkeypatch, age_days=200)
    ciphertext = (vault.path / (sid + ".cbrv")).read_bytes()
    staging = vault.path / ("." + sid + "-feedfeedfeedfeed.part")
    staging.write_bytes(ciphertext)
    staging.chmod(0o600)
    plan = vault.retention_preview(PASSWORD, 90)
    vault.retention_apply(plan, PASSWORD, confirmed=True)
    assert not vault.list_ids()
    assert staging.read_bytes() == ciphertext
