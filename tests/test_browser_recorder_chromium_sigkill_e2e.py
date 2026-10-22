"""Crash recovery with *real headed Chromium*, fake local bookstore only.

The test deliberately SIGKILLs the Playwright/Python process group while HTTP
capture is active. Only the last AES-GCM snapshot may be recovered. A separate
explicit encrypted diagnostic export is checked by an independent reader.
No real provider, person, account, upload or plaintext ZIP is involved.
"""
from __future__ import annotations

from hashlib import sha256
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import sleep
from urllib.parse import urlsplit

import pytest
pytest.importorskip("playwright.sync_api")
pytest.importorskip("cryptography")

from tests.test_browser_recorder_installed_chromium_e2e import (
    local_bookstore, ACCOUNT, TOKEN, SOURCE,
)

PASSPHRASE = "FICTIONAL-CHROMIUM-SIGKILL-KEY-2026"


def test_real_chromium_process_kill_recovers_encrypted_partial_session(
    tmp_path, local_bookstore, monkeypatch,
):
    server, finished_fake_site, calls = local_bookstore
    origin = f"http://127.0.0.1:{server.server_port}"
    folder = tmp_path / "private-vault"
    child_code = r"""
from pathlib import Path
import sys
from threading import Event
from urllib.parse import urlsplit

import browser_recorder_http as capture
from browser_recorder_vault import PrivateSessionVault
from browser_recorder import site_origin as actual_origin

origin = sys.argv[1]
port = int(urlsplit(origin).port)
def exact_fixture(value):
    parsed = urlsplit(value)
    if (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            and parsed.port == port
            and parsed.username is None and parsed.password is None):
        return origin
    return actual_origin(value)

# Only this test's random-port localhost origin bypasses HTTPS validation.
# The production code and other hosts retain the regular policy.
capture.site_origin = exact_fixture
writer = PrivateSessionVault(Path(sys.argv[2])).begin(sys.argv[3])
capture.record_http(
    origin + "/catalog", Event(),
    vault_writer=writer, max_seconds=30,
)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        (str(SOURCE), env.get("PYTHONPATH", ""))
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, origin, str(folder), PASSPHRASE],
        env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert finished_fake_site.wait(25), (
            "Synthetic Chromium sequence did not complete."
        )
        # The collector seals periodic snapshots during its 200 ms loop.
        sleep(1.5)
        assert process.poll() is None, (
            "Chromium capture terminated before the forced crash."
        )
    finally:
        if process.poll() is None:
            # Kill *both* Playwright driver and Chromium; do not trigger Stop,
            # page.close(), browser.close(), context managers or atexit.
            os.killpg(process.pid, signal.SIGKILL)
        _stderr = process.communicate(timeout=12)[1]
        assert ACCOUNT.encode() not in _stderr
        assert TOKEN.encode() not in _stderr
    assert process.returncode == -signal.SIGKILL
    assert calls.count(("POST", "/login")) == 1
    assert calls.count(("GET", "/api/books")) == 1
    assert calls.count(("GET", "/done")) == 1

    # The vault API revalidates URL origins when decrypting. Allow exactly
    # the same synthetic origin in this test process, never a broad http flag.
    monkeypatch.syspath_prepend(str(SOURCE))
    import browser_recorder_http as capture
    from browser_recorder import site_origin as actual_origin

    def exact_fixture(value):
        parsed = urlsplit(value)
        if (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                and parsed.port == server.server_port
                and parsed.username is None and parsed.password is None):
            return origin
        return actual_origin(value)

    monkeypatch.setattr(capture, "site_origin", exact_fixture)
    from browser_recorder_vault import PrivateSessionVault, MAGIC
    vault = PrivateSessionVault(folder)
    ids = vault.list_ids()
    assert len(ids) == 1
    path = folder / (ids[0] + ".cbrv")
    before = path.read_bytes()
    assert before.startswith(MAGIC)
    assert path.stat().st_mode & 0o777 == 0o600
    assert ACCOUNT.encode() not in before
    assert TOKEN.encode() not in before
    assert not list(folder.glob("*.har")) and not list(folder.glob("*.part"))
    restored = vault.open(ids[0], PASSPHRASE)
    try:
        assert restored.state == "recording"
        assert restored.recovery_status == "partial"
        assert restored.trace.end_reason is None
        assert len(restored.trace.exchanges) >= 7
        statuses = {x.status for x in restored.trace.exchanges}
        assert {401, 403, 429}.issubset(statuses)
        assert any(x.method == "POST" for x in restored.trace.exchanges)
        assert restored.private_summary()["origin"] == origin
        from browser_recorder_review import IdentifierReview
        from browser_recorder_zip import prepare_reviewed_zip
        review = IdentifierReview(restored.trace)
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        preview = prepare_reviewed_zip(review).preview()
        assert "plugin-dev-kit/manifest.json" in {
            item["name"] for item in preview["members"]
        }
        assert sha256(path.read_bytes()).digest() == sha256(before).digest()
        assert not list(folder.glob("*.zip"))
        # #2698: recovery is also usable if public review/projection fails.
        # Export only on this explicit synthetic user decision, to a separate
        # 0700 folder, with a distinct passphrase. Never write plaintext ZIP.
        from browser_recorder_private_zip import save_private_zip
        destination = tmp_path / "chosen-private-export"
        destination.mkdir(mode=0o700)
        export_key = "FICTIONAL-RECOVERED-SESSION-EXPORT-KEY-2026"
        name = save_private_zip(restored, destination, export_key, confirmed=True)
        output = destination / name
        assert output.stat().st_mode & 0o777 == 0o600
        reader = r'''
import sys, json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
expected = json.load(sys.stdin)
data = Path(sys.argv[1]).read_bytes()
assert data[:8] == b"CFRZIP01"
key = Scrypt(salt=data[8:24], length=32, n=2**15, r=8, p=1).derive(expected["key"].encode())
plain = AESGCM(key).decrypt(data[24:36], data[36:], data[:36])
with ZipFile(BytesIO(plain)) as archive:
    assert archive.testzip() is None
    observed = json.loads(archive.read("trace.json"))
assert observed["state"] == "recording"
assert observed["end_reason"] is None
assert observed["exchanges"] == expected["exchanges"]
'''
        child = subprocess.run(
            [sys.executable, "-I", "-c", reader, str(output)],
            input=json.dumps({"key": export_key, "exchanges": [
                asdict(entry) for entry in restored.trace.exchanges
            ]}), text=True, capture_output=True, timeout=15,
        )
        assert child.returncode == 0, "Independent private export verification failed"
        assert not child.stdout and not child.stderr
        assert path.read_bytes() == before
        assert not list(destination.glob("*.zip"))
    finally:
        restored.trace.destroy()
