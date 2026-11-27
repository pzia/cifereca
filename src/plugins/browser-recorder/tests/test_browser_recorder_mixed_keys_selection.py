"""Recorder review cannot enumerate or open sessions outside its scope."""
from types import SimpleNamespace as NS
from hashlib import sha256

import pytest

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import VaultError
from cifereca.plugin_api.capture_vault import CaptureScope
import browser_recorder_review_ui as review_ui
from test_browser_recorder_review_ui import FakeTasks, Gtk, GLib, ORIGIN, new_test_vault


def test_other_scope_never_listed_or_opened(tmp_path):
    pytest.importorskip("cryptography")
    core, vault = new_test_vault(tmp_path)
    # A different account scope of the same producer; cross-producer isolation
    # is tested at the Core broker boundary, not by coupling plugin suites.
    other = core.for_plugin("ui.page.browser-recorder")
    scope = CaptureScope("fixture", "instance", "account", "identity")
    foreign = other.begin(scope, "fixture-other-scope", consented=True)
    foreign.save(b"fictional scoped capture", "completed", force=True)
    foreign_path = tmp_path / "vault" / (foreign.capture_id + ".cbrv")
    before = sha256(foreign_path.read_bytes()).digest()
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(1, "GET", ORIGIN + "/fixture", status=200))
    writer = vault.begin(consented=True)
    writer.save(trace, "completed", force=True)
    trace.destroy()
    tasks = FakeTasks()
    panel = review_ui.PrivateReviewPanel(
        Gtk, GLib, tasks, NS(get_text=lambda: ORIGIN),
        NS(get_active=lambda: True), lambda: False, vault=vault,
    )
    try:
        assert panel.refresh_sessions()
        tasks.handles[-1].finish()
        assert list(panel.session_list.options) == [writer.session_id]
        with pytest.raises(VaultError, match="vault_scope_denied"):
            vault.read(foreign.capture_id)
        with pytest.raises(VaultError, match="vault_scope_denied"):
            vault.set_label(foreign.capture_id, "forbidden")
        with pytest.raises(VaultError, match="vault_scope_denied"):
            vault.delete(foreign.capture_id, confirmed=True)
        assert sha256(foreign_path.read_bytes()).digest() == before
        assert not list(tmp_path.rglob("*.zip"))
    finally:
        panel.destroy()
        core.close()
