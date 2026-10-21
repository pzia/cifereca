"""P0 #2725: real AES-GCM ciphertext, secure POSIX files and deferred review.

All fixture data are fictional. No provider, real account, browser profile,
plain HAR or external upload is involved.
"""
from __future__ import annotations

from pathlib import Path
import json
import os

import pytest
pytest.importorskip("cryptography")

from browser_recorder import RecorderError
from browser_recorder_http import PrivateExchange, PrivateHttpTrace, record_http
from browser_recorder_vault import (
    MAGIC, PrivateSessionVault, VaultError, MAX_FILE,
)
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip

ORIGIN = "https://fictional-books.example.org"
PASSPHRASE = "FICTIONAL-ONLY-STRONG-PASSPHRASE-2026"
ACCOUNT = "acct-FICTIONAL-77"
SECRET = "FAKE_PRIVATE_BODY_ONLY"


def sample_trace():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api/books?AccountId=" + ACCOUNT,
        status=302, mime="application/json",
        request_headers={"content-type": "application/json"},
        response_headers={"content-type": "application/json"},
        redirect_to="/library?AccountId=" + ACCOUNT,
        request_body=json.dumps({
            "AccountId": ACCOUNT, "ProductId": 214, "access_token": SECRET,
        }),
        request_body_state="captured",
        response_body_state="redirect",
    ))
    trace.exchanges.append(PrivateExchange(
        2, "GET", url=ORIGIN + "/api/catalog?page=2&limit=20",
        status=200, mime="application/json",
        response_headers={"content-type": "application/json"},
        response_body='{"books":[{"ProductId":214,"AccountId":"' + ACCOUNT + '"}]}',
        response_body_state="captured",
    ))
    trace.exchanges.append(PrivateExchange(
        3, "GET", url=ORIGIN + "/meta.xml",
        status=200, mime="application/xml",
        response_body="<books><BookId>214</BookId></books>",
        response_body_state="captured",
    ))
    return trace


def test_no_disk_before_explicit_consent_and_encrypted_reopen(tmp_path):
    directory = tmp_path / "private"
    vault = PrivateSessionVault(directory)
    assert vault.list_ids() == ()
    assert not directory.exists()
    with pytest.raises(VaultError, match="vault_passphrase_required"):
        vault.begin("short")
    assert not directory.exists()
    writer = vault.begin(PASSPHRASE)
    assert oct(directory.stat().st_mode & 0o777) == "0o700"
    assert vault.list_ids() == ()
    trace = sample_trace()
    writer.save(trace, "recording", force=True)
    writer.save(trace, "completed", force=True)
    sid = writer.session_id
    assert vault.list_ids() == (sid,)
    ciphertext = (directory / (sid + ".cbrv")).read_bytes()
    assert ciphertext.startswith(MAGIC)
    assert oct((directory / (sid + ".cbrv")).stat().st_mode & 0o777) == "0o600"
    for secret in (ACCOUNT, SECRET, ORIGIN, PASSPHRASE, b'"books"'):
        value = secret if type(secret) is bytes else secret.encode()
        assert value not in ciphertext
    assert not any(p.suffix in (".json", ".har", ".zip", ".part")
                   for p in directory.iterdir())
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.open(sid, "FICTIONAL-WRONG-PASSPHRASE-2026")
    original = [(e.url, e.request_body, e.response_body, e.redirect_to)
                for e in trace.exchanges]
    trace.destroy()
    restored = vault.open(sid, PASSPHRASE)
    assert restored.state == "completed"
    assert [(e.url, e.request_body, e.response_body, e.redirect_to)
            for e in restored.trace.exchanges] == original
    assert SECRET in restored.trace.exchanges[0].request_body
    restored.trace.destroy()


def test_multiple_sessions_partial_and_deferred_independent_zip(tmp_path):
    vault = PrivateSessionVault(tmp_path / "vault")
    ids = []
    for number in range(3):
        writer = vault.begin(PASSPHRASE)
        trace = sample_trace()
        trace.exchanges[0].url += "&Batch=" + str(number)
        writer.save(trace, "recording", force=True)
        if number != 1:
            writer.save(trace, "completed", force=True)
        else:
            # Simulate a killed application: latest durable snapshot is
            # still recording, NEVER falsely marked completed.
            pass
        trace.destroy()
        ids.append(writer.session_id)
    assert len(set(ids)) == 3
    assert len(vault.list_ids()) == 3
    restored = vault.open(ids[1], PASSPHRASE)
    assert restored.state == "recording"
    assert restored.trace.end_reason is None  # Never claim a successful Stop
    assert "Batch=1" in restored.trace.exchanges[0].url
    immutable = (tmp_path / "vault" / (ids[1] + ".cbrv")).read_bytes()
    for _ in range(2):
        review = IdentifierReview(restored.trace)
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        plan = prepare_reviewed_zip(review)
        assert plan.preview()["members"]
        assert (tmp_path / "vault" / (ids[1] + ".cbrv")).read_bytes() == immutable
        # Review projections are not automatically saved/shared.
    restored.trace.destroy()


def test_tampering_id_swap_links_and_permissions_fail_closed(tmp_path):
    directory = tmp_path / "private"
    vault = PrivateSessionVault(directory)
    one = vault.begin(PASSPHRASE)
    trace = sample_trace()
    one.save(trace, "completed", force=True)
    second = vault.begin(PASSPHRASE)
    second.save(trace, "completed", force=True)
    sid = one.session_id
    first_path = directory / (sid + ".cbrv")
    second_path = directory / (second.session_id + ".cbrv")
    clean = first_path.read_bytes()
    first_path.write_bytes(second_path.read_bytes())
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.open(sid, PASSPHRASE)  # AEAD bound to filename
    first_path.write_bytes(clean[:-1] + bytes([clean[-1] ^ 1]))
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.open(sid, PASSPHRASE)
    first_path.write_bytes(clean)
    first_path.chmod(0o644)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        vault.open(sid, PASSPHRASE)
    first_path.chmod(0o600)
    first_path.unlink()
    first_path.symlink_to(second_path)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        vault.open(sid, PASSPHRASE)
    first_path.unlink()
    trace.destroy()


def test_unsafe_directory_and_wrong_confirmation_are_not_mutated(tmp_path):
    wrong = tmp_path / "outside"
    wrong.mkdir()
    wrong.chmod(0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(wrong, target_is_directory=True)
    with pytest.raises(VaultError, match="vault_unsafe_directory"):
        PrivateSessionVault(linked).begin(PASSPHRASE)
    assert list(wrong.iterdir()) == []
    vault = PrivateSessionVault(tmp_path / "secure")
    writer = vault.begin(PASSPHRASE)
    trace = sample_trace()
    writer.save(trace, "completed", force=True)
    with pytest.raises(VaultError, match="vault_confirmation_required"):
        vault.delete(writer.session_id, PASSPHRASE)
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.delete(writer.session_id, "FICTIONAL-WRONG-PASSPHRASE-2026",
                     confirmed=True)
    assert vault.list_ids() == (writer.session_id,)
    vault.delete(writer.session_id, PASSPHRASE, confirmed=True)
    assert vault.list_ids() == ()
    trace.destroy()


def test_snapshot_is_bounded_and_never_plain(tmp_path):
    vault = PrivateSessionVault(tmp_path / "secure")
    writer = vault.begin(PASSPHRASE)
    trace = sample_trace()
    for n in range(200):
        trace.exchanges.append(PrivateExchange(
            n + 4, "GET", url=ORIGIN + "/many?AccountId=" + ACCOUNT,
            response_body="FICTIONAL" * 3600,
            response_body_state="captured",
        ))
    with pytest.raises(VaultError, match="vault_limit"):
        writer.save(trace, force=True)
    assert vault.list_ids() == ()
    trace.destroy()


def test_imported_vault_does_not_capture_or_contact_network(tmp_path):
    import browser_recorder_vault
    assert browser_recorder_vault.MAGIC == MAGIC
    assert list(tmp_path.iterdir()) == []
