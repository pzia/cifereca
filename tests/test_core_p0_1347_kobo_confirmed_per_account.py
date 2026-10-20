"""P0 #1347: production Sync callbacks confirm one Kobo account before acquisition.

No actual provider request, filesystem mutation, or token logging.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from tests import test_core_ui_1347_sync_action_scope as harness


class Dialog:
    shown = []

    def __init__(self, window, title, body):
        self.title, self.body = title, body
        self.handlers = {}
        self.buttons = {}
        self.presented = False
        self.__class__.shown.append(self)

    @classmethod
    def new(cls, window, title, body):
        return cls(window, title, body)

    def add_response(self, code, label):
        self.buttons[code] = label

    def set_response_appearance(self, *args):
        pass

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def present(self):
        self.presented = True

    def choose(self, choice):
        self.handlers["response"](self, choice)


def _counts(missing=2):
    return {
        "already_local_linked": 4, "already_local_verified": 1,
        "needs_reconciliation": 0, "candidate_unverified": 0,
        "potentially_missing": missing, "attention_required": 0,
        "blocked_scope": 0, "blocked_unlinked": 0,
        "not_downloadable": 0, "invalid_manifest_items": 0,
    }


def _prepared(missing=2, **changes):
    facts = {
        "status": "prepared",
        "confirmation_token": "d" * 64,
        "expires_in_seconds": 600,
        "requires_explicit_confirmation": True,
        "acquisition_authorized": False,
        "manifest_refreshed": True,
        "counts": _counts(missing),
        "private": "/home/PRIVATE/borrowed?token=SECRET",
    }
    facts.update(changes)
    return NS(ok=True, facts=facts)


def fixture(monkeypatch, sources=None):
    Dialog.shown.clear()
    methods = harness._methods()
    env = methods["_refresh_sync_page"].__globals__
    env["Adw"].MessageDialog = Dialog
    env["Adw"].ResponseAppearance = NS(SUGGESTED=1)
    monkeypatch.setattr(harness, "_methods", lambda: methods)
    window, broker, calls, build, page = harness._fixture(sources=sources)
    def prepare(sid):
        calls.append(("prepare", sid))
        return _prepared()
    def acquire(sid, token, control):
        calls.append(("acquire", sid, token))
        assert control.cancelled() is False
        control.report(current=1, message="/PRIVATE/filename")
        return NS(ok=True, changed=1, facts={"status": "success"})
    broker.prepare_local_materialization = prepare
    broker.materialize_source = acquire
    window._refresh_sync_page()
    return window, broker, calls


def controls(window, idx=0, *, source_id=None):
    groups = window._sync_sources_box.children
    if source_id is None:
        group = groups[idx]
    else:
        matched = [group for group in groups
                   if group._cifereca_sync_source_id == source_id]
        assert len(matched) == 1, "Kobo controls must belong to exactly one account"
        group = matched[0]
    source_row = group.children[0]
    acquisition_row = group.children[3]
    # Keep the new mutating controls on their *own* row. Otherwise the
    # crowded source row becomes thousands of pixels tall in real GTK.
    assert len(source_row.suffixes) == 4
    assert len(acquisition_row.suffixes) == 2
    return acquisition_row.suffixes[0], acquisition_row.suffixes[1], acquisition_row


def test_no_download_on_page_open_refresh_or_preparation(monkeypatch):
    w, broker, calls = fixture(monkeypatch)
    download, cancel, output = controls(w)
    assert download.sensitive and not cancel.sensitive
    assert not any(c[0] in {"prepare", "acquire"} for c in calls)
    download.click()
    assert w.runner.tasks[-1].options["kind"] == "sync-preflight"
    assert "Aucun téléchargement" in output.subtitle
    task = w.runner.tasks[-1]
    assert not any(c[0] in {"prepare", "acquire"} for c in calls)
    assert task.work().ok
    assert ("prepare", "kobo-one") in calls
    assert not any(c[0] == "acquire" for c in calls)
    task.done(_prepared())
    assert Dialog.shown[-1].presented
    assert "manquants possibles : 2" in Dialog.shown[-1].body
    assert "SECRET" not in Dialog.shown[-1].body
    Dialog.shown[-1].choose("cancel")
    assert len(w.runner.tasks) == 1
    assert not any(c[0] == "acquire" for c in calls)


def test_explicit_dialog_runs_only_chosen_account_and_safe_progress(monkeypatch):
    sources = [
        harness._source(source_id="kobo-one", label="Deux Kobo"),
        harness._source(source_id="kobo-two", label="Deux Kobo"),
    ]
    w, broker, calls = fixture(monkeypatch, sources=sources)
    download, cancel, output = controls(w, idx=0)
    download.click()
    w.runner.tasks[-1].done(w.runner.tasks[-1].work())
    Dialog.shown[-1].choose("run")
    assert cancel.sensitive
    assert len(w.runner.tasks) == 2
    task = w.runner.tasks[-1]
    assert task.options["kind"] == "kobo-acquisition"
    assert task.options["with_progress"] is True
    labels = []
    result = task.work(labels.append)
    assert ("acquire", "kobo-one", "d" * 64) in calls
    assert not any(c[0] == "acquire" and c[1] == "kobo-two" for c in calls)
    assert labels == ["Téléchargement Kobo · 1 fichier(s) acquis"]
    task.done(result)
    assert "1 fichier(s) acquis durablement" in output.subtitle
    assert ("refresh-library", False) in calls
    assert not cancel.sensitive


def test_aba_identity_change_blocks_old_dialog_and_queued_acquisition(monkeypatch):
    w, broker, calls = fixture(monkeypatch)
    w._identity_context_generation = 4
    w._refresh_sync_page()
    download, cancel, output = controls(w)
    download.click()
    task = w.runner.tasks[-1]
    task.done(task.work())
    w._identity_context_generation = 6
    Dialog.shown[-1].choose("run")
    assert len(w.runner.tasks) == 1
    assert not any(c[0] == "acquire" for c in calls)


def test_cancel_before_worker_entry_skips_any_mutation(monkeypatch):
    w, broker, calls = fixture(monkeypatch)
    download, cancel, output = controls(w)
    download.click()
    task = w.runner.tasks[-1]
    task.done(task.work())
    Dialog.shown[-1].choose("run")
    cancel.click()
    assert not cancel.sensitive
    task = w.runner.tasks[-1]
    assert task.work(lambda _label: None) is None
    task.done(None)
    assert not any(c[0] == "acquire" for c in calls)
    assert "interrompue" in output.subtitle


@pytest.mark.parametrize("bad", [
    {"confirmation_token": "PRIVATE-TOKEN"},
    {"manifest_refreshed": False},
    {"acquisition_authorized": True},
    {"requires_explicit_confirmation": False},
    {"counts": {"potentially_missing": 2}},
    {"expires_in_seconds": 0},
])
def test_invalid_preparation_cannot_authorize_download(monkeypatch, bad):
    w, broker, calls = fixture(monkeypatch)
    download, cancel, output = controls(w)
    download.click()
    task = w.runner.tasks[-1]
    task.done(_prepared(**bad))
    assert not Dialog.shown
    assert len(w.runner.tasks) == 1
    assert not any(c[0] == "acquire" for c in calls)
    assert "Aucun téléchargement" in output.subtitle


def test_empty_prepared_manifest_does_not_show_download_confirmation(monkeypatch):
    w, broker, calls = fixture(monkeypatch)
    download, _, output = controls(w)
    download.click()
    w.runner.tasks[-1].done(_prepared(missing=0))
    assert Dialog.shown == []
    assert "Aucun fichier à télécharger" in output.subtitle


def test_disconnected_kobo_and_other_providers_cannot_start_download(monkeypatch):
    providers = [
        harness._source(source_id="ko", connected=False),
        harness._source(source_id="pa", provider_id="paris"),
    ]
    w, broker, calls = fixture(monkeypatch, sources=providers)
    # Account-first ordering puts connected Paris ahead of disconnected Kobo.
    # Neither the account index nor the adjacent provider may decide
    # which source owns the (disabled) acquisition control.
    assert w._sync_sources_box.children[0]._cifereca_sync_source_id == "pa"
    assert not controls(w, source_id="ko")[0].sensitive
    paris = next(group for group in w._sync_sources_box.children
                 if group._cifereca_sync_source_id == "pa")
    assert len(paris.children[0].suffixes) == 2
    controls(w, source_id="ko")[0].click()
    assert w.runner.tasks == []


def test_backend_preparation_failure_is_redacted_before_taskrunner(monkeypatch):
    w, broker, calls = fixture(monkeypatch)
    def fail(_sid):
        raise RuntimeError("https://PRIVATE/?token=SECRET")
    broker.prepare_local_materialization = fail
    controls(w)[0].click()
    task = w.runner.tasks[-1]
    with pytest.raises(RuntimeError) as exc:
        task.work()
    assert str(exc.value) == "Préparation Kobo indisponible"
    assert exc.value.__suppress_context__


def test_partial_result_never_looks_like_success(monkeypatch):
    w, broker, calls = fixture(monkeypatch)
    button, _, output = controls(w)
    button.click()
    task = w.runner.tasks[-1]
    task.done(task.work())
    Dialog.shown[-1].choose("run")
    w.runner.tasks[-1].done(NS(
        ok=False, changed=1, facts={"status": "partial", "completion_unknown": True},
    ))
    assert "Résultat incomplet" in output.subtitle
    assert "terminé" not in output.subtitle.lower()
