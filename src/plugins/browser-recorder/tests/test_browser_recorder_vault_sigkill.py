"""Hard-kill recovery and independent re-review of *encrypted* sessions.

The child is SIGKILLed (no finally/atexit/context cleanup); three entirely
fictional local captures, no browser launch or provider credentials.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest
pytest.importorskip("cryptography")

from browser_recorder_vault import PrivateSessionVault, MAGIC
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip

PASSPHRASE = "FICTIONAL-SIGKILL-VAULT-KEY-2026"
ORIGIN = "https://fixture-only.example.org"
FAKE_ACCOUNT = "ACCT-FICTIONAL-99"


def _child_crash_code():
    return r"""
import os
from pathlib import Path
import signal
import sys

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import PrivateSessionVault

vault = PrivateSessionVault(Path(sys.argv[1]))
key = sys.argv[2]
origin = sys.argv[3]
account = sys.argv[4]
for index in range(3):
    writer = vault.begin(key)
    trace = PrivateHttpTrace((origin,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=origin + "/catalog?account=" + account,
        status=200, mime="application/json",
        response_body='{"ProductId":214,"AccountId":"' + account + '"}',
        response_body_state="captured",
    ))
    writer.save(trace, "recording", force=True)
    trace.exchanges.append(PrivateExchange(
        2, "POST", url=origin + "/details?id=" + str(index),
        status=302, redirect_to="/item/" + str(index),
        request_body='{"ProductId":214}',
        request_body_state="captured", response_body_state="redirect",
    ))
    writer.save(trace, "recording", force=True)
    if index < 2:
        writer.save(trace, "completed", force=True)
    else:
        # SIGKILL is synchronous: the third snapshot is durable while the
        # normal finally/Stop and final state transition never execute.
        os.kill(os.getpid(), signal.SIGKILL)
    trace.destroy()
"""


def test_sigkill_recovers_last_durable_snapshot_and_independent_reviews(tmp_path):
    vault_dir = tmp_path / "vault"
    plugin_dir = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(plugin_dir), environment.get("PYTHONPATH", ""))
    )
    child = subprocess.run(
        [sys.executable, "-c", _child_crash_code(), str(vault_dir),
         PASSPHRASE, ORIGIN, FAKE_ACCOUNT],
        env=environment,
        cwd=tmp_path,
        capture_output=True, text=True,
        timeout=25,
    )
    assert child.returncode == -signal.SIGKILL
    assert FAKE_ACCOUNT not in child.stdout + child.stderr
    assert child.stdout == child.stderr == ""
    vault = PrivateSessionVault(vault_dir)
    ids = vault.list_ids()
    assert len(ids) == 3
    assert not list(vault_dir.glob("*.part"))
    assert not any(p.suffix == ".har" or p.suffix == ".zip"
                   for p in vault_dir.iterdir())
    before = {
        identity: sha256((vault_dir / (identity + ".cbrv")).read_bytes()).hexdigest()
        for identity in ids
    }
    sessions = [vault.open(identity, PASSPHRASE) for identity in ids]
    try:
        assert sorted(s.recovery_status for s in sessions) == [
            "completed", "completed", "partial",
        ]
        for s in sessions:
            assert len(s.trace.exchanges) == 2
            assert s.private_summary()["origin"] == ORIGIN
            assert FAKE_ACCOUNT not in repr(s)
            assert s.saved_at
        partial, = (s for s in sessions if s.recovery_status == "partial")
        assert partial.state == "recording" and partial.trace.end_reason is None
        assert [e.method for e in partial.trace.exchanges] == ["GET", "POST"]
        assert partial.private_summary()["exchanges"] == 2
        # Two LLM projections from the same immutable partial snapshot.
        reviewed_hashes = []
        for choice in ("anonymize", "exclude"):
            review = IdentifierReview(partial.trace)
            for candidate in review.candidates():
                review.decide(candidate.key, choice)
            result = prepare_reviewed_zip(review)
            preview = result.preview()
            assert preview["members"]
            reviewed_hashes.append(preview["sha256"])
        assert reviewed_hashes[0] != reviewed_hashes[1]
        assert before == {
            identity: sha256(
                (vault_dir / (identity + ".cbrv")).read_bytes()
            ).hexdigest() for identity in ids
        }
        # All original HTTP URLs and bodies remain cipher-only on disk.
        for identity in ids:
            payload = (vault_dir / (identity + ".cbrv")).read_bytes()
            assert payload.startswith(MAGIC)
            assert FAKE_ACCOUNT.encode() not in payload
            assert ORIGIN.encode() not in payload
    finally:
        for s in sessions:
            s.trace.destroy()


def test_unlocked_summary_is_private_and_never_claims_completion(tmp_path):
    from browser_recorder_http import PrivateExchange, PrivateHttpTrace
    vault = PrivateSessionVault(tmp_path / "private")
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", ORIGIN + "/account?secret=" + FAKE_ACCOUNT, status=200,
    ))
    writer = vault.begin(PASSPHRASE)
    writer.save(trace, "interrupted", force=True)
    trace.destroy()
    restored = vault.open(writer.session_id, PASSPHRASE)
    try:
        private = restored.private_summary()
        assert private["status"] == "interrupted"
        assert private["exchanges"] == 1
        assert private["origin"] == ORIGIN
        assert restored.trace.end_reason is None
        assert FAKE_ACCOUNT not in repr(restored)
        assert list(tmp_path.glob("*.json")) == []
    finally:
        restored.trace.destroy()
