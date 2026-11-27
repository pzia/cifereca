"""Recovery of encrypted atomic-write leftovers; no provider/account data.

SIGKILL can leave .part/.labelpart in the 0700 vault. These *ciphertext*
files count toward the hard quota and are removed by Core retention
after at least 24 hours. Never touch .cbrv or .cbrn.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time

import pytest
pytest.importorskip("cryptography")

import cifereca.core.capture_vault as vault_api
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from capture_engine_fixture import (
    MIN_STAGING_AGE_SECONDS, FixtureVault, VaultError,
)

KEY = "FICTIONAL-ENCRYPTED-LEFTOVERS-SECRET-2026"
ORIGIN = "https://fixture-only-books.example.org"


def _session(tmp_path):
    vault = FixtureVault(tmp_path / "vault")
    writer = vault.begin(KEY)
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/private?account=FICTIONAL-ACCOUNT",
        status=200, response_body='{"private":"FICTIONAL-BODY"}',
        response_body_state="captured",
    ))
    writer.save(trace, "completed", force=True)
    trace.destroy()
    return vault, writer.session_id


def _orphan(vault, sid, ext, *, old=True):
    original = (vault.path / (sid + ".cbrv")).read_bytes()
    path = vault.path / ("." + sid + "-dabbad00deadbeef." + ext)
    path.write_bytes(original)
    path.chmod(0o600)
    if old:
        mtime = time.time() - MIN_STAGING_AGE_SECONDS - 3600
        os.utime(path, (mtime, mtime))
    return path


def prune(vault):
    return vault.auto_prune(KEY, older_than_days=90, pinned=lambda *_: False)


def test_staging_preview_missing_does_not_create_directory(tmp_path):
    vault = FixtureVault(tmp_path / "missing")
    assert vault.staging_summary() == (0, 0, 0)
    assert not vault.path.exists()
    assert prune(vault) == (0, 0)
    assert not vault.path.exists()


def test_automatic_purge_old_ciphertext_only_preserves_sessions_and_labels(tmp_path):
    vault, sid = _session(tmp_path)
    vault.set_label(sid, KEY, "Fictional private name")
    source = vault.path / (sid + ".cbrv")
    label = vault.path / (sid + ".cbrn")
    source_bytes, label_bytes = source.read_bytes(), label.read_bytes()
    old = _orphan(vault, sid, "part")
    old_label = _orphan(vault, sid, "labelpart")
    young = vault.path / ("." + sid + "-1234567890abcdef.part")
    young.write_bytes(source_bytes)
    young.chmod(0o600)
    assert vault.staging_summary() == (
        3, old.stat().st_size + old_label.stat().st_size
        + young.stat().st_size, 2,
    )
    assert all(item.exists() for item in (source, label, old, old_label, young))
    assert prune(vault) == (0, 0)
    assert not old.exists() and not old_label.exists()
    assert young.exists()
    assert vault.staging_summary()[0] == 1
    assert source.read_bytes() == source_bytes and label.read_bytes() == label_bytes
    assert vault.get_label(sid, KEY) == "Fictional private name"
    stored = vault.open(sid, KEY)
    assert stored.recovery_status == "completed"
    stored.trace.destroy()
    assert vault.list_ids() == (sid,)
    assert not any(p.suffix == ".json" for p in vault.path.iterdir())


def test_orphan_ciphertext_counts_toward_quota_until_maintenance(tmp_path, monkeypatch):
    vault, sid = _session(tmp_path)
    path = _orphan(vault, sid, "part")
    source = vault.path / (sid + ".cbrv")
    total = path.stat().st_size + source.stat().st_size
    monkeypatch.setattr(vault_api, "MAX_VAULT_BYTES", total - 1)
    # Low-level begin does not bypass the hard cap. Core maintenance owns purge.
    with pytest.raises(VaultError, match="vault_limit"):
        vault.begin(KEY)
    assert path.exists() and source.exists()
    assert vault.staging_summary()[2] == 1
    prune(vault)
    writer = vault.begin(KEY)
    # Later writes still have to honor their own limit, independently.
    assert writer.session_id != sid


def test_symlinks_or_hardlinks_block_all_purge(tmp_path):
    vault, sid = _session(tmp_path)
    good = _orphan(vault, sid, "part")
    candidate = vault.path / ("." + sid + "-feedfeedfeedfeed.labelpart")
    outside = tmp_path / "dont-touch"
    outside.write_bytes(b"OUTSIDE-FICTIONAL")
    candidate.symlink_to(outside)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        vault.staging_summary()
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        prune(vault)
    assert good.exists() and candidate.is_symlink()
    assert outside.read_bytes() == b"OUTSIDE-FICTIONAL"
    candidate.unlink()
    os.link(good, candidate)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        prune(vault)
    assert good.exists() and candidate.exists()
    candidate.unlink()
    assert prune(vault) == (0, 0)
    assert not good.exists()


def test_stage_cleanup_does_not_follow_other_names_or_paths(tmp_path):
    vault, sid = _session(tmp_path)
    source = vault.path / (sid + ".cbrv")
    other = vault.path / "not-recorder.stagepart"
    other.write_bytes(b"FICTIONAL-METADATA")
    unknown = vault.path / ("." + sid + "-not-hex.part")
    unknown.write_bytes(b"FICTIONAL-METADATA-2")
    assert prune(vault) == (0, 0)
    assert other.read_bytes() == b"FICTIONAL-METADATA"
    assert unknown.read_bytes() == b"FICTIONAL-METADATA-2"
    assert source.exists()
