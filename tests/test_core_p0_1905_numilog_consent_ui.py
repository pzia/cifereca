"""#1905: headless GTK acceptance for explicit Numilog backup consent."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


class Dialog:
    def __init__(self, *args):
        self.args = args
        self.responses = {}
        self.handlers = {}
    def add_response(self, key, label):
        self.responses[key] = label
    def set_response_appearance(self, *args):
        pass
    def connect(self, name, callback):
        self.handlers[name] = callback
    def present(self):
        pass
    def respond(self, choice):
        self.handlers["response"](self, choice)


def _ui(*, initial="manual", malformed=False):
    sources = [
        _source(identity_id="alice", source_id="numilog-consent-one",
                provider_id="numilog", label="Boutique Numilog",
                enabled=True, connected=True, sync_capable=True,
                sync_capabilities=("remote_inventory",)),
    ]
    w, broker, calls, build, _page = _fixture(sources=sources)
    dialogs = []
    methods = next(
        cell.cell_contents for cell in build.__closure__
        if isinstance(cell.cell_contents, dict)
        and "_refresh_sync_page" in cell.cell_contents
    )
    adw = methods["_refresh_sync_page"].__globals__["Adw"]
    adw.MessageDialog = NS(new=lambda *args: dialogs.append(Dialog(*args)) or dialogs[-1])
    adw.ResponseAppearance = NS(SUGGESTED=1, DESTRUCTIVE=2)
    state = {"mode": initial}
    counts = {
        "owned_purchases": 2, "already_local_linked": 1,
        "already_local_verified": 1, "needs_file_verification": 0,
        "potentially_missing": 1, "candidate_unverified": 1,
        "needs_reconciliation": 0, "attention_required": 0,
        "not_downloadable": 0, "ignored_unattested_rights": 0,
    }
    def mode(sid):
        calls.append(("materialization-mode", sid))
        return state["mode"]
    def prepare(sid):
        calls.append(("materialization-prepare", sid))
        return NS(ok=True, facts={
            "phase": "materialize", "status": "confirmation_required",
            "acquisition_authorized": False, "requires_explicit_confirmation": True,
            "manifest_refreshed": False, "files_verified": True,
            "schedule_available": True, "confirmation_token": (
                "NOT-VALID" if malformed else "a" * 64
            ), "counts": dict(counts),
        })
    def confirm(sid, token, *, mode):
        calls.append(("materialization-confirm", sid, token, mode))
        assert token == "a" * 64 and mode == "on_start"
        state["mode"] = "on_start"
        return NS(ok=True, facts={
            "status": "on_start", "acquisition_authorized": False,
        })
    def revoke(sid):
        calls.append(("materialization-revoke", sid))
        state["mode"] = "manual"
        return "manual"
    broker.numilog_materialization_schedule = mode
    broker.prepare_numilog_materialization_consent = prepare
    broker.confirm_numilog_materialization_consent = confirm
    broker.revoke_numilog_materialization_consent = revoke
    w._refresh_sync_page()
    def row():
        return next(
            item for item in w._sync_sources_box.children[0].children
            if getattr(item, "title", "") == "Sauvegarde automatique des achats Numilog"
        )
    return w, broker, calls, dialogs, state, row


def test_consent_ui_requires_preview_then_explicit_second_confirmation():
    w, _broker, calls, dialogs, state, row = _ui()
    assert "Mode manuel" in row().subtitle
    assert w.runner.tasks == []
    row().suffixes[0].click()
    assert state["mode"] == "manual"
    assert not dialogs
    task = w.runner.tasks[-1]
    task.done(task.work())
    assert len(dialogs) == 1 and "Autoriser" in dialogs[0].responses["confirm"]
    assert "Compte" in dialogs[0].args[2]
    assert "1" in dialogs[0].args[2]
    assert state["mode"] == "manual"
    dialogs[0].respond("confirm")
    task = w.runner.tasks[-1]
    assert state["mode"] == "manual"
    task.done(task.work())
    assert state["mode"] == "on_start"
    assert "prochain démarrage" in row().subtitle
    assert [name for name, *_ in calls if name == "materialization-confirm"] == [
        "materialization-confirm"
    ]


@pytest.mark.parametrize("change", ["identity", "refresh", "close"])
def test_preview_stale_scope_never_opens_or_confirms_consent(change):
    w, _broker, calls, dialogs, state, row = _ui()
    row().suffixes[0].click()
    task = w.runner.tasks[-1]
    if change == "identity":
        w.service.current_identity = NS(identity_id="bob")
    elif change == "refresh":
        w._refresh_sync_page()
    else:
        w._closed = True
    task.done(task.work())
    assert dialogs == [] and state["mode"] == "manual"
    assert not any(name == "materialization-confirm" for name, *_ in calls)


def test_corrupt_confirmation_token_fails_closed_without_dialog():
    w, _broker, calls, dialogs, state, row = _ui(malformed=True)
    row().suffixes[0].click()
    task = w.runner.tasks[-1]
    task.done(task.work())
    assert dialogs == [] and state["mode"] == "manual"
    assert "invalide" in row().subtitle
    assert not any(name == "materialization-confirm" for name, *_ in calls)


def test_disconnect_can_still_revoke_durable_source_policy():
    w, broker, calls, dialogs, state, row = _ui(initial="on_start")
    assert "Autorisé" in row().subtitle
    row().suffixes[0].click()
    assert len(dialogs) == 1
    dialogs[0].respond("revoke")
    task = w.runner.tasks[-1]
    task.done(task.work())
    assert state["mode"] == "manual"
    assert "Mode manuel" in row().subtitle
    assert ("materialization-revoke", "numilog-consent-one") in calls
