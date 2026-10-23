"""P0 #2884: a new capture must not unlock a differently keyed archive.

Only fictional AES-GCM snapshots and an in-process fake GTK panel.
No network calls, original provider data, plaintext log or automatic ZIP.
"""
from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("cryptography")

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import PrivateSessionVault, VaultError
import browser_recorder_review_ui as review_ui
from test_browser_recorder_review_ui import FakeTasks, Gtk, GLib, ORIGIN

KEY_A = "FICTIONAL-SELECTOR-KEY-A-2026"
KEY_B = "FICTIONAL-SELECTOR-KEY-B-2026"


def _save(vault, key, number):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=f"{ORIGIN}/synthetic/{number}", status=200,
        mime="application/json", response_body='{"BookId":214}',
        response_body_state="captured", response_body_reason="captured",
    ))
    writer = vault.begin(key)
    writer.save(trace, "completed", force=True)
    sid = writer.session_id
    trace.destroy()
    return sid


def test_key_a_new_capture_does_not_unlock_key_b_or_relabel_it(tmp_path):
    vault_path = tmp_path / "vault"
    vault = PrivateSessionVault(vault_path)
    first = _save(vault, KEY_A, 1)
    foreign = _save(vault, KEY_B, 2)
    foreign_path = vault_path / (foreign + ".cbrv")
    foreign_hash = sha256(foreign_path.read_bytes()).digest()

    tasks = FakeTasks()
    panel = review_ui.PrivateReviewPanel(
        Gtk, GLib, tasks,
        NS(get_text=lambda: ORIGIN + "/synthetic"),
        NS(get_active=lambda: True),
        legacy_running=lambda: False,
        vault_directory=vault_path,
    )
    try:
        assert panel.refresh_sessions()
        assert len(panel.session_list.options) == 2
        assert all(ORIGIN not in label for label in panel.session_list.options.values())
        panel.vault_passphrase.set_text(KEY_A)
        panel.unlock_sessions()
        tasks.handles[-1].finish()
        assert panel.session_list.get_active_id() in {first, foreign}
        panel.session_list.set_active_id(foreign)
        assert "verrouillée/illisible" in panel.session_list.options[panel.session_list.get_active_id()]
        assert ORIGIN not in panel.session_list.options[panel.session_list.get_active_id()]
        panel.open_session()
        assert "Ouverture refusée" in panel.status.label
        assert panel._trace is None

        third = _save(vault, KEY_A, 3)
        assert sha256(foreign_path.read_bytes()).digest() == foreign_hash
        assert panel.refresh_sessions()
        # A manual refresh cannot silently switch away from selection B.
        assert panel.session_list.get_active_id() == foreign
        panel.unlock_sessions()
        tasks.handles[-1].finish()
        assert "verrouillée/illisible" in panel.session_list.options[foreign]
        assert panel.session_list.get_active_id() == foreign
        for sid in (first, third):
            panel.session_list.set_active_id(sid)
            assert ORIGIN in panel.session_list.options[panel.session_list.get_active_id()]
            restored = vault.open(sid, KEY_A)
            assert restored.trace.exchanges[0].response_body == '{"BookId":214}'
            restored.trace.destroy()
        with pytest.raises(VaultError):
            vault.open(foreign, KEY_A)
        recovered_b = vault.open(foreign, KEY_B)
        recovered_b.trace.destroy()
        assert sha256(foreign_path.read_bytes()).digest() == foreign_hash
        assert not list(tmp_path.rglob("*.zip"))
    finally:
        panel.destroy()
