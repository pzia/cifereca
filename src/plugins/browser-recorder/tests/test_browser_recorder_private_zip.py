"""P0 #2698: encrypted diagnostic export is separate from reviewed public ZIP.

Independent AES-256-GCM/Scrypt implementation verifies interoperability.
Only fictional identifiers, never a real provider or account.
"""
from __future__ import annotations

from io import BytesIO
import json
from zipfile import ZipFile
import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import PrivateSessionVault, VaultError
from browser_recorder_private_zip import (
    MAGIC, save_private_zip, decrypt_private_zip,
)
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip

ORIGIN = "https://fictional-bookshop.example.org"
PASSPHRASE = "FICTIONAL-ARCHIVE-SECRET-2026"
ACCOUNT = "acct-FICTIONAL-88"
TOKEN = "FAKE_TOKEN_IN_JSON_FOR_SECURITY_TEST"


def stored(tmp_path):
    vault = PrivateSessionVault(tmp_path / "vault")
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", ORIGIN + "/login?AccountId=" + ACCOUNT,
        status=302, redirect_to="/catalog?AccountId=" + ACCOUNT,
        request_body=json.dumps({
            "AccountId": ACCOUNT, "access_token": TOKEN, "ProductId": 214,
        }),
        request_headers={"content-type": "application/json"},
        request_body_state="captured",
        response_body_state="redirect",
    ))
    trace.exchanges.append(PrivateExchange(
        2, "GET", ORIGIN + "/books?Page=2&Limit=20",
        status=200, mime="application/xml",
        response_headers={"content-type": "application/xml"},
        response_body="<Books><BookId>214</BookId></Books>",
        response_body_state="captured",
    ))
    writer = vault.begin(PASSPHRASE)
    writer.save(trace, "completed", force=True)
    trace.destroy()
    return vault, vault.open(writer.session_id, PASSPHRASE)


def independent_decrypt(encrypted):
    """No Cifereca decrypt function used: published format only."""
    salt, nonce = encrypted[8:24], encrypted[24:36]
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        PASSPHRASE.encode("utf-8")
    )
    return AESGCM(key).decrypt(nonce, encrypted[36:], MAGIC + salt + nonce)


def test_explicit_private_export_and_external_read(tmp_path):
    vault, session = stored(tmp_path)
    folder = tmp_path / "chosen"
    folder.mkdir(mode=0o700)
    with pytest.raises(VaultError, match="private_zip_confirmation_required"):
        save_private_zip(session, folder, PASSPHRASE)
    assert not list(folder.iterdir())
    source = (tmp_path / "vault" / (session.session_id + ".cbrv")).read_bytes()
    filename = save_private_zip(session, folder, PASSPHRASE, confirmed=True)
    assert filename.endswith(".brzip")
    result = folder / filename
    assert result.stat().st_mode & 0o777 == 0o600
    raw = result.read_bytes()
    assert raw.startswith(MAGIC)
    for value in (ACCOUNT, TOKEN, ORIGIN, PASSPHRASE):
        assert value.encode("utf-8") not in raw
    unwrapped = independent_decrypt(raw)
    assert unwrapped == decrypt_private_zip(raw, PASSPHRASE)
    with ZipFile(BytesIO(unwrapped)) as zip_file:
        assert zip_file.testzip() is None
        assert set(zip_file.namelist()) == {"README.txt", "trace.json"}
        detail = json.loads(zip_file.read("trace.json"))
        assert detail["saved_at"] == session.saved_at
        rows = detail["exchanges"]
        assert rows[0]["url"].endswith("AccountId=" + ACCOUNT)
        assert json.loads(rows[0]["request_body"])["access_token"] == TOKEN
        assert rows[0]["redirect_to"] == "/catalog?AccountId=" + ACCOUNT
        assert rows[1]["response_body"] == "<Books><BookId>214</BookId></Books>"
        assert rows[0]["request_headers"] == {"content-type": "application/json"}
        assert "authorization" not in rows[0]["request_headers"]
        assert "cookie" not in rows[0]["request_headers"]
    assert (tmp_path / "vault" / (session.session_id + ".cbrv")).read_bytes() == source
    # Independent reviewed publication policy is unaffected by private export.
    review = IdentifierReview(session.trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
    preview = prepare_reviewed_zip(review).preview()
    joined = "\n".join(item["text"] for item in preview["members"])
    assert ACCOUNT not in joined and TOKEN not in joined
    assert not any(path.suffix == ".zip" for path in folder.iterdir())
    session.trace.destroy()


def test_wrong_key_and_tampering_not_accepted(tmp_path):
    _, session = stored(tmp_path)
    folder = tmp_path / "chosen"
    folder.mkdir(mode=0o700)
    filename = save_private_zip(session, folder, PASSPHRASE, confirmed=True)
    raw = (folder / filename).read_bytes()
    with pytest.raises(VaultError, match="private_zip_unlock_failed"):
        decrypt_private_zip(raw, "FICTIONAL-WRONG-PASSPHRASE")
    with pytest.raises(VaultError, match="private_zip_unlock_failed"):
        decrypt_private_zip(raw[:-1] + bytes((raw[-1] ^ 1,)), PASSPHRASE)
    session.trace.destroy()


def test_destination_must_be_preselected_private_and_no_overwrite(tmp_path):
    _, session = stored(tmp_path)
    nonprivate = tmp_path / "shared"
    nonprivate.mkdir(mode=0o755)
    with pytest.raises(VaultError, match="private_zip_directory"):
        save_private_zip(session, nonprivate, PASSPHRASE, confirmed=True)
    missing = tmp_path / "missing"
    with pytest.raises(VaultError, match="private_zip_write_failed"):
        save_private_zip(session, missing, PASSPHRASE, confirmed=True)
    assert not missing.exists()
    symlink = tmp_path / "link"
    symlink.symlink_to(nonprivate, target_is_directory=True)
    with pytest.raises(VaultError, match="private_zip_write_failed"):
        save_private_zip(session, symlink, PASSPHRASE, confirmed=True)
    assert not list(nonprivate.iterdir())
    session.trace.destroy()
