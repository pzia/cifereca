"""Kill the writer *between ciphertext fsync and atomic rename*.

No real provider, browser, identity, token, file export or plaintext staging.
This verifies the exact crash window that leaves encrypted orphan files.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
pytest.importorskip("cryptography")

from browser_recorder_vault import (
    PrivateSessionVault, MAGIC, LABEL_MAGIC, MIN_STAGING_AGE_SECONDS,
)

KEY = "FICTIONAL-ATOMIC-SIGKILL-PASSPHRASE-2026"
ORIGIN = "https://fictional-test-only.example.org"
PRIVATE = "FAKE_ACCOUNT_ATOMIC_BOUNDARY_99"

_CHILD = r"""
import os, signal, sys
from pathlib import Path
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
import browser_recorder_vault as vault_api

directory, passphrase, origin, private, case = sys.argv[1:]
vault = vault_api.PrivateSessionVault(Path(directory))
writer = vault.begin(passphrase)
trace = PrivateHttpTrace((origin,))
trace.exchanges.append(PrivateExchange(
    1, "GET", origin + "/catalog?AccountId=" + private,
    status=200, response_body='{"private":"' + private + '"}',
    response_body_state="captured",
))
if case != "first":
    writer.save(trace, "recording", force=True)
    trace.exchanges.append(PrivateExchange(
        2, "POST", origin + "/details", status=302,
        request_body='{"AccountId":"' + private + '"}',
        request_body_state="captured",
    ))

def killed_before_rename(*_args, **_kwargs):
    # Called only AFTER encrypted data is written and fsynced to .part.
    os.kill(os.getpid(), signal.SIGKILL)

vault_api.os.replace = killed_before_rename
if case == "label":
    writer.save(trace, "completed", force=True)  # would also SIGKILL
else:
    writer.save(trace, "recording", force=True)
"""

# For a label crash, finish the source BEFORE installing the kill hook.
_LABEL_CHILD = _CHILD.replace(
    'vault_api.os.replace = killed_before_rename\nif case == "label":\n    writer.save(trace, "completed", force=True)  # would also SIGKILL\nelse:\n    writer.save(trace, "recording", force=True)',
    '''writer.save(trace, "completed", force=True)
vault_api.os.replace = killed_before_rename
vault.set_label(writer.session_id, passphrase, "FAKE PRIVATE LABEL")''',
)


def _crash(tmp_path, case):
    directory = tmp_path / "vault"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        (str(Path(__file__).resolve().parents[1]),
         env.get("PYTHONPATH", ""))
    )
    child = subprocess.run(
        [sys.executable, "-c", _LABEL_CHILD if case == "label" else _CHILD,
         str(directory), KEY, ORIGIN, PRIVATE, case],
        cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=35,
    )
    assert child.returncode == -signal.SIGKILL
    assert child.stdout == child.stderr == ""
    vault = PrivateSessionVault(directory)
    return vault, list(directory.iterdir())


@pytest.mark.parametrize("case", ["first", "subsequent", "label"])
def test_sigkill_after_ciphertext_fsync_never_loses_last_commit_or_leaks(
    tmp_path, case,
):
    vault, files = _crash(tmp_path, case)
    sidlist = vault.list_ids()
    assert len(sidlist) == (0 if case == "first" else 1)
    summary = vault.staging_summary()
    assert summary[0] == summary[2] == 1 if False else summary[0] == 1
    assert summary[1] > 36 and summary[2] == 0  # fresh: no silent cleanup
    for path in files:
        assert PRIVATE not in path.name and ORIGIN not in path.name
        assert path.stat().st_mode & 0o777 == 0o600
        raw = path.read_bytes()
        assert PRIVATE.encode() not in raw and ORIGIN.encode() not in raw
        if path.suffix in {".part", ".cbrv"}:
            assert raw.startswith(MAGIC)
        elif path.suffix in {".labelpart", ".cbrn"}:
            assert raw.startswith(LABEL_MAGIC)
    capture = None
    if sidlist:
        capture = vault.path / (sidlist[0] + ".cbrv")
        original = capture.read_bytes()
        restored = vault.open(sidlist[0], KEY)
        try:
            assert restored.recovery_status == (
                "completed" if case == "label" else "partial"
            )
            assert len(restored.trace.exchanges) == 1
            assert PRIVATE in restored.trace.exchanges[0].url
            assert restored.trace.end_reason is None
        finally:
            restored.trace.destroy()
        assert vault.get_label(sidlist[0], KEY) == ""
    assert vault.prune_staging(confirmed=True) == (0, 0)
    staged = [x for x in files if x.suffix in {".part", ".labelpart"}]
    assert len(staged) == 1
    old = time.time() - MIN_STAGING_AGE_SECONDS - 3600
    os.utime(staged[0], (old, old))
    assert vault.staging_summary()[2] == 1
    count, size = vault.prune_staging(confirmed=True)
    assert count == 1 and size > 36
    assert not staged[0].exists()
    assert vault.staging_summary() == (0, 0, 0)
    if capture is not None:
        assert capture.read_bytes() == original
        assert vault.list_ids() == tuple(sidlist)
    else:
        assert vault.list_ids() == ()
