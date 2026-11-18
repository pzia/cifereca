"""#4681: saved reading feedback is per-profile, explicit and never a default."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "src/plugins/recommendations/recommendations.py").read_text(encoding="utf-8")


def test_saved_preference_optin_starts_off_and_reuses_core_contract_only():
    assert 'Gtk.CheckButton(label="Utiliser mes appréciations")' in SOURCE
    assert 'self._preference_optin.set_active(False)' in SOURCE
    assert 'self._preference_optin.connect("toggled", self._preference_optin_changed)' in SOURCE
    assert '"use_saved_explicit_preferences": True' in SOURCE
    assert 'if use_preferences else {}' in SOURCE
    assert 'and self.selected_identity_id() == identity_id' in SOURCE
    assert 'reference is None' in SOURCE


def test_profile_and_reference_changes_revoke_optin_without_implied_click():
    assert 'def _clear_preference_optin(self)' in SOURCE
    assert 'self._resetting_preference_optin = True' in SOURCE
    assert 'if not getattr(self, "_resetting_preference_optin", False):' in SOURCE
    assert 'if profile_changed:' in SOURCE
    assert 'self._clear_preference_optin()' in SOURCE
    assert 'optin.set_sensitive(reference is None)' in SOURCE
    assert 'optin.set_sensitive(False)' in SOURCE


def test_no_automatic_saved_feedback_in_reference_path():
    source = SOURCE.split('def operation(control):', 1)[1].split(
        'handle = task_broker.submit(', 1
    )[0]
    reference, whole_library = source.split('return compute_recommendations(', 1)
    assert 'use_saved_explicit_preferences' not in reference
    assert '"use_saved_explicit_preferences": True' in whole_library


def test_feedback_editor_has_explicit_confirmation_and_async_cas_guards():
    assert 'Gtk.Button(label="Mes appréciations…")' in SOURCE
    assert 'self._feedback_button.connect("clicked", self._manage_feedback)' in SOURCE
    assert 'dialog.add_response("liked", "J’aime")' in SOURCE
    assert 'dialog.add_response("not_interested", "Peu intéressant")' in SOURCE
    assert 'dialog.add_response("clear", "Retirer cet avis")' in SOURCE
    assert 'confirm.add_response("erase", "Effacer les avis")' in SOURCE
    assert 'user_confirmed=True, clear_all=clear_all' in SOURCE
    assert 'expected_revision=revision' in SOURCE
    assert 'generation != self._generation' in SOURCE


def test_preference_targets_require_exact_identity_and_unique_local_key():
    import sys
    repo = Path(__file__).resolve().parents[1]
    plugin = str(repo / "src/plugins/recommendations")
    if plugin not in sys.path:
        sys.path.insert(0, plugin)
    from recommendations import _explicit_feedback_targets

    rows = [
        {"key": "artifact:a", "identity_id": "alice",
         "title": "Titre", "author": "A"},
        {"key": "artifact:b", "identity_id": "bob",
         "title": "Titre", "author": "A"},
        {"key": "artifact:duplicate", "identity_id": "alice",
         "title": "Un", "author": "A"},
        {"key": "artifact:duplicate", "identity_id": "alice",
         "title": "Deux", "author": "B"},
        {"key": "bad key?", "identity_id": "alice",
         "title": "Autre", "author": "C"},
        {"key": "artifact:injected", "identity_id": "alice",
         "title": "Autre", "author": "C",
         "preference": "liked", "state": "remote_loan_active"},
    ]
    targets = _explicit_feedback_targets(rows, "alice")
    keys = [key for key, _label in targets]
    assert "artifact:a" in keys
    assert "artifact:injected" in keys
    assert "artifact:b" not in keys
    assert "artifact:duplicate" not in keys
    assert "bad key?" in keys  # The exact Core key is opaque; title is not key.
    assert _explicit_feedback_targets(rows, "bob")[0][0] == "artifact:b"
    assert _explicit_feedback_targets(rows, "") == ()


def test_feedback_worker_persists_exact_identity_after_navigation_and_preserves_cas(tmp_path):
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace as NS

    from cifereca.brokers import MetadataBrokerImpl
    from cifereca.core.explicit_reading_preferences import preference_snapshot
    from cifereca.services import LibraryService
    from cifereca.storage import Storage

    repo = Path(__file__).resolve().parents[1]
    plugin_path = str(repo / "src/plugins/recommendations")
    if plugin_path not in sys.path:
        sys.path.insert(0, plugin_path)
    from recommendations import RecommendationsPlugin, UxRecommendationsView

    submitted, idle, toasts, refreshes = [], [], [], []
    class Tasks:
        def submit(self, operation, **_kw):
            handle = NS(operation=operation, value=None, error=None)
            def result():
                if handle.error is not None:
                    raise handle.error
                return handle.value
            handle.result = result
            handle.add_done_callback = lambda callback: setattr(handle, "callback", callback)
            submitted.append(handle)
            return handle

    tasks = Tasks()
    context = NS(broker=lambda name: tasks)
    plugin = RecommendationsPlugin()
    plugin.activate(context)
    storage = Storage(tmp_path / "feedback.sqlite")
    identities = {"alice": NS(identity_id="alice"), "bob": NS(identity_id="bob")}
    service = NS(
        current_identity=identities["alice"], storage=storage,
        identity_by_id=identities.get,
        library_entries=lambda *, identity_id, scan: [
            {"identity_id": identity_id, "key": "artifact:a"},
        ],
    )
    service.change_reading_preference = lambda **kw: LibraryService.change_reading_preference(service, **kw)
    broker = MetadataBrokerImpl(service)

    view = UxRecommendationsView.__new__(UxRecommendationsView)
    view._disposed = False
    view._generation = 10
    view._active_identity_id = "alice"
    view._feedback_write_pending = False
    view._feedback_button = NS(set_sensitive=lambda _flag: None)
    view.plugin = plugin
    view.host = NS(toast=toasts.append)
    view.GLib = NS(idle_add=lambda cb: idle.append(cb))
    view.refresh = lambda: refreshes.append(True)
    view.selected_identity_id = lambda: service.current_identity.identity_id

    def admit(revision, preference="liked", *, clear_all=False):
        view._submit_feedback(
            broker=broker, identity_id="alice", library_key=None if clear_all else "artifact:a",
            preference=preference, revision=revision, clear_all=clear_all,
        )
        return submitted[-1]

    def navigate(identity):
        service.current_identity = identities[identity]
        view._active_identity_id = identity
        view._generation += 1

    def run(pool, handle, *, cancelled=False):
        try:
            handle.value = pool.submit(handle.operation, NS(cancelled=lambda: cancelled)).result()
        except Exception as exc:
            handle.error = exc
        handle.callback(handle)
        while idle:
            idle.pop(0)()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = admit(0)
            navigate("bob")
            run(pool, first)
            assert first.error is None
            assert preference_snapshot(storage, "alice").preferences == (("artifact:a", "liked"),)
            assert preference_snapshot(storage, "bob").revision == 0
            assert not toasts and not refreshes
            assert not view._feedback_write_pending

            # An older Alice dialog cannot be admitted from Bob at all.
            admit(1)
            assert len(submitted) == 1
            navigate("alice")
            second = admit(1, None, clear_all=True)
            navigate("bob")
            navigate("alice")
            run(pool, second)
            assert second.error is None
            assert preference_snapshot(storage, "alice").revision == 2
            assert preference_snapshot(storage, "alice").preferences == ()
            assert not toasts and not refreshes

            stale = admit(1)
            run(pool, stale)
            assert "revision_changed" in str(stale.error)
            assert preference_snapshot(storage, "alice").revision == 2

            cancelled = admit(2)
            run(pool, cancelled, cancelled=True)
            assert preference_snapshot(storage, "alice").revision == 2

            revoked = admit(2)
            owner = identities.pop("alice")
            run(pool, revoked)
            assert "identity_unknown" in str(revoked.error)
            assert preference_snapshot(storage, "alice").revision == 2
            identities["alice"] = owner

            last = admit(2)
            plugin.deactivate()
            plugin.activate(context)  # ABA of even the same plugin context
            run(pool, last)
            assert last.value is None
            assert preference_snapshot(storage, "alice").revision == 2
            assert preference_snapshot(storage, "bob").revision == 0
    finally:
        storage.close()


def test_local_documentary_only_mode_is_separate_explicit_profile_optin():
    assert 'label="Sources : fichiers locaux vérifiés"' in SOURCE
    assert 'self._local_only_optin.set_active(False)' in SOURCE
    assert 'self._local_only_optin.connect(' in SOURCE
    assert '"toggled", self._local_only_optin_changed,' in SOURCE
    assert 'def _clear_local_only_optin(self)' in SOURCE
    assert 'self._resetting_local_only_optin = True' in SOURCE
    assert 'self._clear_local_only_optin()' in SOURCE
    assert 'documentary_source_policy": "attested_local"' in SOURCE
    assert 'if local_only else {}' in SOURCE
    assert 'and self.selected_identity_id() == identity_id' in SOURCE
    assert 'documentary.set_sensitive(reference is None)' in SOURCE
    assert 'documentary.set_sensitive(False)' in SOURCE


def test_local_only_programmatic_reset_never_counts_as_user_consent():
    import sys
    from types import SimpleNamespace as NS
    repo = Path(__file__).resolve().parents[1]
    plugin = str(repo / "src/plugins/recommendations")
    if plugin not in sys.path:
        sys.path.insert(0, plugin)
    from recommendations import UxRecommendationsView

    calls = []
    state = {"checked": True}
    view = UxRecommendationsView.__new__(UxRecommendationsView)
    view._resetting_local_only_optin = False
    view.refresh = lambda: calls.append("refresh")
    view._local_only_optin = NS(
        get_active=lambda: state["checked"],
        set_active=lambda value: (
            state.__setitem__("checked", value),
            view._local_only_optin_changed(),
        ),
    )
    view._clear_local_only_optin()
    assert state["checked"] is False
    assert calls == []
    assert view._resetting_local_only_optin is False
    view._local_only_optin_changed()
    assert calls == ["refresh"]


def test_feedback_editor_reads_fresh_core_books_even_with_no_ranking_result():
    import sys
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    repo = Path(__file__).resolve().parents[1]
    plugin = str(repo / "src/plugins/recommendations")
    if plugin not in sys.path:
        sys.path.insert(0, plugin)
    from recommendations import _fresh_feedback_targets

    source = Mock()
    source.library_entries.return_value = [
        {"key": "artifact:a", "identity_id": "alice",
         "title": "Livre local", "author": "A"},
        {"key": "artifact:other", "identity_id": "bob",
         "title": "Livre étranger", "author": "B"},
        {"key": "artifact:duplicate", "identity_id": "alice",
         "title": "Duplicata un", "author": "C"},
        {"key": "artifact:duplicate", "identity_id": "alice",
         "title": "Duplicata deux", "author": "C"},
    ]
    targets = _fresh_feedback_targets(source, "alice")
    assert tuple(key for key, _ in targets) == ("artifact:a",)
    source.library_entries.assert_called_once_with(
        identity_id="alice", scan=False,
    )
    assert "Livre local" in targets[0][1]
    # Neither provider nor ranking results are an input to this editor.
    assert not source.search.called
    assert not source.catalog_scan.called


def test_feedback_editor_refuses_missing_or_corrupt_snapshot_without_stale_cache():
    import sys
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    repo = Path(__file__).resolve().parents[1]
    plugin = str(repo / "src/plugins/recommendations")
    if plugin not in sys.path:
        sys.path.insert(0, plugin)
    from recommendations import _fresh_feedback_targets

    assert _fresh_feedback_targets(NS(), "alice") == ()
    reader = Mock()
    reader.library_entries.return_value = {"key": "not-a-list"}
    assert _fresh_feedback_targets(reader, "alice") == ()
    reader.library_entries.return_value = [
        {"key": "artifact:b", "identity_id": "bob", "title": "Other"},
    ]
    assert _fresh_feedback_targets(reader, "alice") == ()
    reader.library_entries.side_effect = RuntimeError(
        "private provider bearer never shown to user"
    )
    import pytest
    with pytest.raises(RuntimeError):
        _fresh_feedback_targets(reader, "alice")


def test_manage_feedback_does_not_depend_on_completed_recommendations_or_scan():
    assert "targets = _fresh_feedback_targets(broker, identity_id)[:2048]" in SOURCE
    assert "rows = reader(identity_id=identity_id, scan=False)" in SOURCE
    segment = SOURCE.split("def _manage_feedback(self, *_args)", 1)[1].split(
        "def _submit_feedback(", 1
    )[0]
    assert "self._reference_books, identity_id" not in segment
    assert "catalog_broker" not in segment
    assert "catalog_scan(" not in segment
