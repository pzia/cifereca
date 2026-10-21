"""P0 #2725: user-defined labels are AEAD sidecars, never plaintext or ZIP."""
from __future__ import annotations

from hashlib import sha256
import os

import pytest
pytest.importorskip("cryptography")

from browser_recorder_http import PrivateHttpTrace, PrivateExchange
from browser_recorder_vault import PrivateSessionVault, VaultError

ORIGIN = "https://fictional-books.example.org"
SECRET = "FAKE_PRIVATE_ACCOUNT-777"
KEY = "FICTIONAL-SESSION-NAME-PASSWORD-2026"
LABEL = "Mes tests Alice — lecture privée"


def vault_with_session(tmp_path):
    vault = PrivateSessionVault(tmp_path / "vault")
    writer = vault.begin(KEY)
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/account?private=" + SECRET,
        status=200, response_body='{"fictional":"data"}',
        response_body_state="captured",
    ))
    writer.save(trace, "completed", force=True)
    trace.destroy()
    return vault, writer.session_id


def test_renaming_encrypts_metadata_and_preserves_original_capture(tmp_path):
    vault, sid = vault_with_session(tmp_path)
    capture = vault.path / (sid + ".cbrv")
    original_sha = sha256(capture.read_bytes()).hexdigest()
    assert vault.get_label(sid, KEY) == ""
    vault.set_label(sid, KEY, LABEL)
    sidecar = vault.path / (sid + ".cbrn")
    assert sidecar.stat().st_mode & 0o777 == 0o600
    assert LABEL.encode() not in sidecar.read_bytes()
    assert ORIGIN.encode() not in sidecar.read_bytes()
    assert SECRET.encode() not in sidecar.read_bytes()
    assert not any(suffix in {".zip", ".json", ".har"}
                   for suffix in (f.suffix for f in vault.path.iterdir()))
    assert vault.get_label(sid, KEY) == LABEL
    index, failures = vault.unlocked_index(KEY)
    assert failures == 0 and index[0]["label"] == LABEL
    assert sha256(capture.read_bytes()).hexdigest() == original_sha
    vault.set_label(sid, KEY, "Autre nom — privé")
    assert vault.get_label(sid, KEY) == "Autre nom — privé"
    assert sha256(capture.read_bytes()).hexdigest() == original_sha
    assert len(vault.list_ids()) == 1
    vault.delete(sid, KEY, confirmed=True)
    assert vault.list_ids() == ()
    assert not sidecar.exists()


@pytest.mark.parametrize("invalid", [
    "", "a\nb", "abc\x00def", "text\u202e", "f" * 81, "fin\rde ligne",
])
def test_invalid_name_never_writes(tmp_path, invalid):
    vault, sid = vault_with_session(tmp_path)
    with pytest.raises(VaultError, match="vault_label_invalid"):
        vault.set_label(sid, KEY, invalid)
    assert not list(vault.path.glob("*.cbrn"))


def test_wrong_password_and_tamper_fail_closed_without_data_loss(tmp_path):
    vault, sid = vault_with_session(tmp_path)
    capture = vault.path / (sid + ".cbrv")
    original = capture.read_bytes()
    with pytest.raises(VaultError, match="vault_unlock_failed"):
        vault.set_label(sid, "FICTIONAL-WRONG-LABEL-PASSWORD", LABEL)
    vault.set_label(sid, KEY, LABEL)
    sidecar = vault.path / (sid + ".cbrn")
    cipher = sidecar.read_bytes()
    sidecar.write_bytes(cipher[:-1] + bytes([cipher[-1] ^ 0x40]))
    with pytest.raises(VaultError, match="vault_label_invalid"):
        vault.get_label(sid, KEY)
    with pytest.raises(VaultError, match="vault_label_invalid"):
        vault.set_label(sid, KEY, "overwrite impossible")
    assert capture.read_bytes() == original
    assert sidecar.read_bytes() != cipher
    index, failures = vault.unlocked_index(KEY)
    assert failures == 0
    assert index[0]["label"] == ""
    assert index[0]["label_unavailable"] is True
    sidecar.unlink()
    # Symlinks are neither read nor replaced by authenticated metadata API.
    foreign = tmp_path / "other"
    foreign.write_bytes(b"FICTIONAL_FOREIGN_FILE")
    sidecar.symlink_to(foreign)
    with pytest.raises(VaultError, match="vault_unsafe_file"):
        vault.get_label(sid, KEY)
    with pytest.raises(VaultError):
        vault.set_label(sid, KEY, LABEL)
    assert foreign.read_bytes() == b"FICTIONAL_FOREIGN_FILE"
    assert capture.read_bytes() == original


def test_label_stays_out_of_the_anonymized_and_private_zip(tmp_path):
    from browser_recorder_review import IdentifierReview
    from browser_recorder_zip import prepare_reviewed_zip
    from browser_recorder_private_zip import save_private_zip, decrypt_private_zip
    from io import BytesIO
    from zipfile import ZipFile

    vault, sid = vault_with_session(tmp_path)
    vault.set_label(sid, KEY, LABEL)
    stored = vault.open(sid, KEY)
    try:
        review = IdentifierReview(stored.trace)
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        plan = prepare_reviewed_zip(review)
        assert LABEL.encode() not in plan._blob
        private = tmp_path / "private"
        private.mkdir(mode=0o700)
        zip_password = "FICTIONAL-EXPORT-KEY-SEPARATE-2026"
        name = save_private_zip(
            stored, private, zip_password, confirmed=True,
        )
        with ZipFile(BytesIO(decrypt_private_zip(
            (private / name).read_bytes(), zip_password
        ))) as zipped:
            assert LABEL.encode() not in zipped.read("trace.json")
    finally:
        stored.trace.destroy()
