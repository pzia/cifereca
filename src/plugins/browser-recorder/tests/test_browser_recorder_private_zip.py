"""#8181: explicit private ZIP is separate from reviewed public ZIP.

Independent standard-library ZIP reader verifies interoperability.
Only fictional identifiers, never a real provider or account.
"""
from __future__ import annotations

from io import BytesIO
from hashlib import sha256
import json
from zipfile import ZipFile
import pytest

pytest.importorskip("cryptography")

from browser_recorder_devkit import core_contract_kit
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from capture_engine_fixture import FixtureVault, VaultError
from browser_recorder_private_zip import (
    save_session_llm_zip,
)
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip

ORIGIN = "https://fictional-bookshop.example.org"
PASSPHRASE = "FICTIONAL-ARCHIVE-SECRET-2026"
ACCOUNT = "acct-FICTIONAL-88"
TOKEN = "FAKE_TOKEN_IN_JSON_FOR_SECURITY_TEST"


def stored(tmp_path):
    vault = FixtureVault(tmp_path / "vault")
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


def test_explicit_private_export_and_external_read(tmp_path):
    vault, session = stored(tmp_path)
    folder = tmp_path / "chosen"
    folder.mkdir(mode=0o700)
    with pytest.raises(VaultError, match="private_zip_confirmation_required"):
        save_session_llm_zip(session, folder / 'chosen.zip')
    assert not list(folder.iterdir())
    source = (tmp_path / "vault" / (session.session_id + ".cbrv")).read_bytes()
    result = folder / "chosen.zip"
    save_session_llm_zip(session, result, confirmed=True)
    assert result.stat().st_mode & 0o777 == 0o600
    with ZipFile(result) as zip_file:
        assert zip_file.testzip() is None
        names = set(zip_file.namelist())
        assert {"README.txt", "trace.json",
                "plugin-dev-kit/PROVIDER-DEVELOPMENT.md",
                "plugin-dev-kit/manifest.json",
                "plugin-dev-kit/contracts/downloadable_resources.py",
                "plugin-dev-kit/contracts/loan_inventory.py"} <= names
        sdk = json.loads(zip_file.read("plugin-dev-kit/manifest.json"))
        assert {row["path"] for row in sdk["files"]} == (
            names - {"README.txt", "trace.json", "plugin-dev-kit/manifest.json"})
        for entry in sdk["files"]:
            assert sha256(zip_file.read(entry["path"])).hexdigest() == entry["sha256"]
        assert b"Core Cifereca" in zip_file.read("plugin-dev-kit/PROVIDER-DEVELOPMENT.md")
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
    assert list(folder.glob("*.zip")) == [result]
    session.trace.destroy()



def test_simple_llm_export_works_in_0755_folder_without_second_secret(tmp_path):
    """Only the vault uses encryption; user Save As produces ordinary ZIP 0600."""
    import os
    from pathlib import Path

    vault, session = stored(tmp_path)
    downloads = tmp_path / "downloads"
    downloads.mkdir(mode=0o755)
    destination = downloads / "session-for-llm.zip"
    vault_blob = (vault.path / (session.session_id + ".cbrv")).read_bytes()
    with pytest.raises(VaultError, match="private_zip_confirmation_required"):
        save_session_llm_zip(session, destination)
    assert not destination.exists()
    size = save_session_llm_zip(session, destination, confirmed=True)
    assert size == destination.stat().st_size
    assert destination.stat().st_mode & 0o777 == 0o600
    with ZipFile(destination) as zf:
        assert zf.testzip() is None
        names = set(zf.namelist())
        assert {"README.txt", "trace.json",
                "plugin-dev-kit/PROVIDER-DEVELOPMENT.md",
                "plugin-dev-kit/manifest.json"} <= names
        kit = core_contract_kit()
        for member in ("plugin-dev-kit/PROVIDER-DEVELOPMENT.md",
                       "plugin-dev-kit/contracts/downloadable_resources.py"):
            assert zf.read(member) == kit[member]
        data = json.loads(zf.read("trace.json"))
        assert ACCOUNT in data["exchanges"][0]["url"]
        assert TOKEN in data["exchanges"][0]["request_body"]
    with pytest.raises(VaultError, match="private_zip_exists"):
        save_session_llm_zip(session, destination, confirmed=True)
    assert destination.read_bytes().startswith(b"PK")
    assert (vault.path / (session.session_id + ".cbrv")).read_bytes() == vault_blob
    assert ACCOUNT.encode() not in vault_blob
    link = downloads / "symlink.zip"
    link.symlink_to(destination)
    with pytest.raises(VaultError, match="private_zip_exists"):
        save_session_llm_zip(session, link, confirmed=True)
    assert link.is_symlink()
    other = tmp_path / "relative.zip"
    with pytest.raises(VaultError, match="private_zip_destination"):
        save_session_llm_zip(session, Path("relative.zip"), confirmed=True)
    with pytest.raises(VaultError, match="private_zip_destination"):
        save_session_llm_zip(session, downloads / "no-extension", confirmed=True)
    session.trace.destroy()

def test_missing_public_core_kit_blocks_private_zip_export(tmp_path, monkeypatch):
    """A missing packaged guide must not produce an incomplete LLM ZIP."""
    import browser_recorder_private_zip as private_zip
    from browser_recorder import RecorderError

    vault, session = stored(tmp_path)
    output = tmp_path / "private"
    output.mkdir(mode=0o700)
    clear = tmp_path / "llm.zip"
    monkeypatch.setattr(
        private_zip, "core_contract_kit",
        lambda: (_ for _ in ()).throw(RecorderError("core_kit_unavailable")),
    )
    with pytest.raises(VaultError, match="private_zip_kit_unavailable"):
        save_session_llm_zip(session, clear, confirmed=True)
    assert not clear.exists()
    assert not list(output.iterdir())
    assert len(vault.list_ids()) == 1
    session.trace.destroy()


# Standalone reader deliberately imports no package named cifereca/browser_recorder.
# Only Python stdlib and the explicitly selected local ZIP are used.
_EXTERNAL_READER = r"""
import io
import json
from pathlib import Path
import sys
from zipfile import ZipFile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

with ZipFile(sys.argv[1]) as archive:
    assert archive.testzip() is None
    names = set(archive.namelist())
    assert {"README.txt", "trace.json",
            "plugin-dev-kit/PROVIDER-DEVELOPMENT.md",
            "plugin-dev-kit/manifest.json"} <= names
    kit_manifest = json.loads(archive.read("plugin-dev-kit/manifest.json"))
    from hashlib import sha256
    for member in kit_manifest["files"]:
        assert sha256(archive.read(member["path"])).hexdigest() == member["sha256"]
    contents = json.loads(archive.read("trace.json"))
    assert contents["schema"] == "cifereca.browser-recorder.private-session/1"
    assert len(contents["exchanges"]) == 2
    first, second = contents["exchanges"]
    assert first["url"].endswith("AccountId=acct-FICTIONAL-88")
    assert first["status"] == 302
    assert first["redirect_to"].endswith("AccountId=acct-FICTIONAL-88")
    assert json.loads(first["request_body"])["access_token"] == (
        "FAKE_TOKEN_IN_JSON_FOR_SECURITY_TEST"
    )
    assert second["response_body"] == "<Books><BookId>214</BookId></Books>"
    assert second["status"] == 200
print("EXTERNAL_PRIVATE_FIXTURE_OK")
"""


def test_private_zip_reopened_in_isolated_external_python_with_no_plugin_import(
    tmp_path,
):
    """Real cross-process format interoperability, without publishing ZIP."""
    import os
    from pathlib import Path
    import subprocess
    import sys

    vault, source = stored(tmp_path)
    output = tmp_path / "operator-chosen-private-directory"
    output.mkdir(mode=0o700)
    protected = output / "session.zip"
    save_session_llm_zip(source, protected, confirmed=True)
    assert protected.stat().st_mode & 0o777 == 0o600
    capture = vault.path / (source.session_id + ".cbrv")
    before = capture.read_bytes()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # forbid relying on plugin source directory

    def external_read(path):
        return subprocess.run(
            [sys.executable, "-I", "-c", _EXTERNAL_READER, str(path)],
            stdin=subprocess.DEVNULL,
            cwd=tmp_path, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
            check=False,
        )

    good = external_read(protected)
    assert good.returncode == 0
    assert good.stdout == b"EXTERNAL_PRIVATE_FIXTURE_OK\n"
    assert good.stderr == b""
    for private in (ACCOUNT, TOKEN, ORIGIN, PASSPHRASE):
        assert private.encode("utf-8") not in good.stdout
        assert private.encode("utf-8") not in before
    # A truncated ZIP is not accepted by an independent reader.
    altered = output / "tampered.zip"
    raw = protected.read_bytes()
    altered.write_bytes(raw[:len(raw)//2])
    invalid = external_read(altered)
    assert invalid.returncode != 0 and not invalid.stdout
    assert capture.read_bytes() == before
    assert len(vault.list_ids()) == 1
    assert set(output.glob("*.zip")) == {protected, altered}
    assert not list(output.glob("*.json"))
    source.trace.destroy()
