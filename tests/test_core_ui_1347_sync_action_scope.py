"""P1 #1347 — identity-scoped source synchronization and stale GTK handlers.

Exercises the actual GTK controller methods with headless widget doubles;
no provider, network, download, source mutation, or GTK runtime needed.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.sync_action_scope import (
    capture_sync_action_scope, sync_action_scope_current,
)
from cifereca.gui.sync_view import (
    sync_identity_action_label, sync_source_presentation, sync_source_attention,
    sync_source_row_labels,
    sync_local_preflight_presentation, sync_local_reconciliation_presentation,
    sync_numilog_local_preview_presentation,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "sync_controller.py"


class Icon:
    def __init__(self, icon_name):
        self.icon_name = icon_name
        self.css_classes = set()
        self.tooltip = None

    @classmethod
    def new_from_icon_name(cls, name):
        return cls(name)

    def set_valign(self, *args): pass
    def set_from_icon_name(self, name): self.icon_name = name
    def set_tooltip_text(self, text): self.tooltip = text
    def add_css_class(self, name): self.css_classes.add(name)


class Widget:
    def __init__(self, label=None, **kwargs):
        self.label = label
        self.children = []
        self.suffixes = []
        self.prefixes = []
        self.css_classes = set()
        self.handlers = {}
        self.sensitive = True
        self.active = False
        self.visible = True
        self.child = None
        self.subtitle = None

    def add_css_class(self, name): self.css_classes.add(name)
    def set_policy(self, *args): pass
    def set_margin_top(self, *args): pass
    set_margin_bottom = set_margin_start = set_margin_end = set_margin_top
    def set_hexpand(self, *args): pass
    def set_vexpand(self, *args): pass
    def set_wrap(self, *args): pass
    def set_expanded(self, value): self.expanded = value
    def set_wrap_mode(self, *args): pass
    def set_width_chars(self, *args): pass
    def set_valign(self, *args): pass
    def set_subtitle_lines(self, *args): pass
    def set_tooltip_text(self, text): self.tooltip = text
    def set_title(self, title): self.title = title
    def set_subtitle(self, subtitle): self.subtitle = subtitle
    def set_label(self, label): self.label = label
    def get_label(self): return self.label
    def set_sensitive(self, sensitive): self.sensitive = sensitive
    def set_visible(self, visible): self.visible = visible
    def get_visible(self): return self.visible
    def set_active(self, active): self.active = active
    def get_active(self): return self.active
    def get_child(self): return self.child
    def set_child(self, child): self.child = child
    def add_prefix(self, widget): self.prefixes.append(widget)
    def add_suffix(self, widget): self.suffixes.append(widget)
    def append(self, widget): self.children.append(widget)
    def add(self, widget): self.children.append(widget)
    def connect(self, signal, callback): self.handlers[signal] = callback
    def click(self):
        if self.sensitive:
            return self.handlers["clicked"](self)
    def toggled(self, active):
        self.active = active
        return self.handlers["notify::active"](self, None)


class Runner:
    def __init__(self):
        self.tasks = []
    def submit(self, label, work, done, error=None, **kwargs):
        task = NS(label=label, work=work, done=done, error=error, options=kwargs)
        self.tasks.append(task)
        return task


def _methods():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    methods = [node for node in tree.body
               if isinstance(node, ast.FunctionDef)
               and node.name in {"_build_sync_page", "_refresh_sync_page"}]
    assert len(methods) == 2
    future = ast.ImportFrom(module="__future__",
                            names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *methods], type_ignores=[]),
    )
    gtk = NS(
        ScrolledWindow=Widget, Box=Widget, Label=Widget, Switch=Widget, Image=Icon,
        Expander=Widget,
        PolicyType=NS(NEVER=0, AUTOMATIC=1),
        Orientation=NS(VERTICAL=1, HORIZONTAL=2),
        Align=NS(CENTER=3),
    )
    adw = NS(PreferencesGroup=Widget, ActionRow=Widget)
    env = dict(
        Gtk=gtk, Adw=adw,
        Pango=NS(WrapMode=NS(WORD_CHAR=1)),
        AdaptiveActionBar=lambda **kwargs: Widget(),
        add_css=lambda widget, *classes: (
            [widget.add_css_class(name) for name in classes] and widget
            if classes else widget
        ),
        clear_box=lambda widget: widget.children.clear(),
        # Callback/scope tests isolate native layout; its real implementation
        # has separate component and GTK allocation acceptance tests.
        add_sync_action=lambda row, button: row.add_suffix(button),
        sync_action_row=lambda row: row,
        # Keep callback tests independent of native progressive layout, just
        # like FlowBox wrapping above. Its widget composition is tested alone.
        add_sync_detail=lambda group, row, **kw: group.add(row),
        sync_identity_action_label=sync_identity_action_label,
        sync_source_presentation=sync_source_presentation,
        sync_source_attention=sync_source_attention,
        sync_source_row_labels=sync_source_row_labels,
        sync_local_preflight_presentation=sync_local_preflight_presentation,
        sync_local_reconciliation_presentation=sync_local_reconciliation_presentation,
        sync_numilog_local_preview_presentation=sync_numilog_local_preview_presentation,
        capture_sync_action_scope=capture_sync_action_scope,
        sync_action_scope_current=sync_action_scope_current,
        SyncRequest=lambda identity_id: NS(identity_id=identity_id),
        TaskPriority=NS(USER_ACTION=2),
    )
    exec(compile(module, str(OWNER), "exec"), env)
    return env


def _source(identity_id="alice", source_id="kobo-one", **options):
    fields = dict(
        identity_id=identity_id, source_id=source_id, provider_id="kobo",
        label="Kobo", sync_capabilities=("local_materialization",),
        enabled=True, connected=True, sync_capable=True,
        status="idle", last_success=None, last_attempt=None, error="",
    )
    fields.update(options)
    return NS(**fields)


def _fixture(identity_id="alice", sources=None):
    methods = _methods()
    calls = []
    identity = NS(identity_id=identity_id, label="Alice") if identity_id else None
    service = NS(current_identity=identity)
    providers = list(sources) if sources is not None else [_source()]
    broker = NS(
        sources=lambda iid: (calls.append(("sources", iid)) or list(providers)),
        set_enabled=lambda sid, value: calls.append(("set-enabled", sid, value)),
        synchronize_source=lambda sid: (calls.append(("sync-source", sid)) or "ok"),
        synchronize=lambda request: (calls.append(("sync-all", request.identity_id)) or "ok"),
        preview_local_materialization=lambda sid: (
            calls.append(("preview", sid)) or NS(ok=False, facts={"status": "not_supported"})
        ),
        audit_local_reconciliation=lambda sid: (
            calls.append(("audit", sid)) or NS(ok=False, facts={"status": "audit_unavailable"})
        ),
    )
    window = NS(
        _closed=False, service=service,
        plugin_manager=NS(broker=lambda kind: broker),
        action_button=lambda label, icon, **kw: Widget(label=label),
        runner=Runner(),
        refresh_library=lambda scan: calls.append(("refresh-library", scan)),
        show_error=lambda msg, title: calls.append(("error", msg, title)),
    )
    window._refresh_sync_page = lambda: methods["_refresh_sync_page"](window)
    build = lambda: methods["_build_sync_page"](window)
    page = build()
    window._refresh_sync_page()
    return window, broker, calls, build, page


def _source_row(window):
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    return row


def test_capture_requires_explicit_identity_and_live_page_token():
    token = object()
    window = NS(_closed=False, _sync_page_token=token,
                service=NS(current_identity=NS(identity_id="alice")))
    scope = capture_sync_action_scope(window, page_token=token)
    assert scope is not None and scope.identity_id == "alice"
    assert sync_action_scope_current(window, scope)
    assert capture_sync_action_scope(window, page_token=object()) is None
    window.service.current_identity = None
    assert not sync_action_scope_current(window, scope)
    assert capture_sync_action_scope(window, page_token=token) is None


def test_capture_requires_same_list_generation_and_valid_identity_type():
    token = object()
    window = NS(_closed=False, _sync_page_token=token,
                _sync_preview_generation=5,
                service=NS(current_identity=NS(identity_id="alice")))
    scope = capture_sync_action_scope(
        window, page_token=token, preview_generation=5,
    )
    assert sync_action_scope_current(window, scope)
    window._sync_preview_generation += 1
    assert not sync_action_scope_current(window, scope)
    assert capture_sync_action_scope(
        window, page_token=token, preview_generation=5,
    ) is None
    window.service.current_identity = NS(identity_id=" ")
    assert capture_sync_action_scope(window, page_token=token) is None
    window.service.current_identity = NS(identity_id=123)
    assert capture_sync_action_scope(window, page_token=token) is None


def test_window_close_or_page_rebuild_revokes_existing_scope():
    window, broker, calls, build, page = _fixture()
    scope = capture_sync_action_scope(
        window, page_token=window._sync_page_token,
    )
    build()
    assert not sync_action_scope_current(window, scope)
    fresh = capture_sync_action_scope(window, page_token=window._sync_page_token)
    assert sync_action_scope_current(window, fresh)
    window._closed = True
    assert not sync_action_scope_current(window, fresh)


def test_no_identity_disables_global_and_source_actions_without_broker_io():
    window, broker, calls, build, page = _fixture(identity_id=None)
    assert window._sync_all_button.sensitive is False
    assert window._sync_sources_box.children[0].label == (
        "Sélectionnez une identité avant de synchroniser."
    )
    window._sync_all_button.click()
    assert window.runner.tasks == []
    assert not any(name.startswith("sync") for name, *rest in calls)


def test_global_sync_request_cannot_fall_back_to_all_identities():
    window, broker, calls, build, page = _fixture()
    window._sync_all_button.click()
    assert len(window.runner.tasks) == 1
    task = window.runner.tasks[0]
    assert task.work() == "ok"
    assert ("sync-all", "alice") in calls
    task.done("ok")
    assert ("refresh-library", False) in calls


def test_global_sync_is_cancelled_before_worker_if_identity_changes():
    window, broker, calls, build, page = _fixture()
    old_button = window._sync_all_button
    old_button.click()
    window.service.current_identity = NS(identity_id="bob")
    task = window.runner.tasks[0]
    assert task.work() is None
    task.done(None)
    assert not any(name == "sync-all" for name, *rest in calls)
    assert not any(name == "refresh-library" for name, *rest in calls)


def test_global_sync_button_from_detached_page_cannot_schedule_work():
    window, broker, calls, build, page = _fixture()
    old_button = window._sync_all_button
    build()
    old_button.click()
    assert window.runner.tasks == []


def test_old_source_button_cannot_enqueue_after_identity_change():
    window, broker, calls, build, page = _fixture()
    old = _source_row(window).suffixes[1]
    window.service.current_identity = NS(identity_id="bob")
    old.click()
    assert window.runner.tasks == []


def test_source_sync_queued_work_and_completion_are_identity_scoped():
    window, broker, calls, build, page = _fixture()
    now = _source_row(window).suffixes[1]
    now.click()
    assert len(window.runner.tasks) == 1
    task = window.runner.tasks[0]
    window.service.current_identity = NS(identity_id="bob")
    assert task.work() is None
    task.done(None)
    assert not any(name == "sync-source" for name, *rest in calls)
    assert not any(name == "refresh-library" for name, *rest in calls)


def test_live_source_sync_still_refreshes_library_after_list_regeneration():
    window, broker, calls, build, page = _fixture()
    _source_row(window).suffixes[1].click()
    task = window.runner.tasks[0]
    assert task.work() == "ok"
    task.done("ok")
    assert ("sync-source", "kobo-one") in calls
    assert ("refresh-library", False) in calls


def test_old_toggle_cannot_write_to_source_of_new_account():
    window, broker, calls, build, page = _fixture()
    toggle = _source_row(window).suffixes[0]
    window.service.current_identity = NS(identity_id="bob")
    toggle.toggled(False)
    assert not any(name == "set-enabled" for name, *rest in calls)


def test_old_toggle_cannot_write_after_refresh_or_page_rebuild():
    window, broker, calls, build, page = _fixture()
    toggle = _source_row(window).suffixes[0]
    window._refresh_sync_page()
    toggle.toggled(False)
    assert not any(name == "set-enabled" for name, *rest in calls)
    build()
    toggle.toggled(True)
    assert not any(name == "set-enabled" for name, *rest in calls)


def test_current_toggle_keeps_per_source_mutation_and_refresh():
    window, broker, calls, build, page = _fixture()
    _source_row(window).suffixes[0].toggled(False)
    assert ("set-enabled", "kobo-one", False) in calls
    assert calls.count(("sources", "alice")) >= 2


def test_toggle_provider_exception_never_leaks_private_details():
    window, broker, calls, build, page = _fixture()
    broker.set_enabled = lambda *args: (_ for _ in ()).throw(
        RuntimeError("https://secret.invalid/?token=TOPSECRET"),
    )
    _source_row(window).suffixes[0].toggled(False)
    assert calls[-1] == (
        "error", "Modification de la source indisponible.", "Synchronisation",
    )
    assert "TOPSECRET" not in repr(calls)


def test_mismatched_broker_source_owner_is_not_rendered_or_enabled():
    window, broker, calls, build, page = _fixture(
        sources=[_source(identity_id="bob", source_id="wrong")],
    )
    assert window._sync_all_button.sensitive is False
    assert window.runner.tasks == []
    assert window._sync_sources_box.children[0].label == (
        "Aucune source synchronisable pour cette identité"
    )


def test_old_kobo_preview_never_schedules_or_delivers_after_identity_change():
    window, broker, calls, build, page = _fixture()
    row = _source_row(window)
    preview = row.suffixes[2]
    output = window._sync_sources_box.children[0].children[1]
    preview.click()
    assert len(window.runner.tasks) == 1
    task = window.runner.tasks[0]
    window.service.current_identity = NS(identity_id="bob")
    assert task.work() is None
    task.done(NS(ok=True, facts={}))
    assert output.subtitle == "Calcul local en cours…"
    assert not any(name == "preview" for name, *rest in calls)


def test_old_kobo_preview_button_after_refresh_is_inert():
    window, broker, calls, build, page = _fixture()
    old = _source_row(window).suffixes[2]
    window._refresh_sync_page()
    old.click()
    assert window.runner.tasks == []


def test_identity_a_b_a_generation_cycle_revokes_prior_scope():
    token = object()
    window = NS(
        _closed=False, _sync_page_token=token,
        _identity_context_generation=4,
        service=NS(current_identity=NS(identity_id="alice")),
    )
    old = capture_sync_action_scope(window, page_token=token)
    assert sync_action_scope_current(window, old)
    window._identity_context_generation += 1
    window.service.current_identity = NS(identity_id="bob")
    window._identity_context_generation += 1
    window.service.current_identity = NS(identity_id="alice")
    assert not sync_action_scope_current(window, old)
    fresh = capture_sync_action_scope(window, page_token=token)
    assert fresh.identity_id == old.identity_id
    assert sync_action_scope_current(window, fresh)


def test_queued_source_work_rejects_identity_a_b_a_even_when_id_repeats():
    window, broker, calls, build, page = _fixture()
    window._identity_context_generation = 11
    # Captured after generation is added, just like MainWindow.
    window._refresh_sync_page()
    _source_row(window).suffixes[1].click()
    task = window.runner.tasks[0]
    window._identity_context_generation = 13
    assert task.work() is None
    task.done(None)
    assert not any(name == "sync-source" for name, *rest in calls)
    assert not any(name == "refresh-library" for name, *rest in calls)


def test_queued_global_sync_rejects_aba_before_broker_execution():
    window, broker, calls, build, page = _fixture()
    window._identity_context_generation = 11
    window._sync_all_button.click()
    task = window.runner.tasks[0]
    window._identity_context_generation = 13
    assert task.work() is None
    assert not any(name == "sync-all" for name, *rest in calls)


def test_source_sync_task_uses_neutral_provider_label_for_unknown_provider():
    window, broker, calls, _build, _page = _fixture(
        sources=[_source(
            provider_id="plugin.future", source_id="future-source",
            label="Bibliothèque future",
        )],
    )
    _source_row(window).suffixes[1].click()
    assert len(window.runner.tasks) == 1
    task = window.runner.tasks[0]
    assert task.label == "Synchronisation Bibliothèque future…"
    assert task.work() == "ok"
    assert ("sync-source", "future-source") in calls


def test_numilog_legacy_sync_uses_same_neutral_task_label():
    window, broker, calls, _build, _page = _fixture(
        sources=[_source(
            provider_id="numilog", source_id="numilog-source",
            label="Mes achats",
        )],
    )
    _source_row(window).suffixes[1].click()
    assert window.runner.tasks[0].label == "Synchronisation Mes achats…"
    assert window.runner.tasks[0].work() == "ok"
    assert ("sync-source", "numilog-source") in calls
