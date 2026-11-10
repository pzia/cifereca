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


def test_feedback_worker_never_writes_after_profile_or_generation_change():
    import sys
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    repo = Path(__file__).resolve().parents[1]
    plugin = str(repo / "src/plugins/recommendations")
    if plugin not in sys.path:
        sys.path.insert(0, plugin)
    from recommendations import UxRecommendationsView

    submitted = []
    class Tasks:
        def submit(self, operation, **_kw):
            submitted.append(operation)
            return NS(add_done_callback=lambda _callback: None)

    view = UxRecommendationsView.__new__(UxRecommendationsView)
    view._disposed = False
    view._generation = 10
    view._active_identity_id = "alice"
    view._feedback_write_pending = False
    view._feedback_button = NS(set_sensitive=lambda _flag: None)
    view.plugin = NS(context=NS(broker=lambda name: Tasks()))
    view.host = NS(toast=lambda _message: None)
    view.selected_identity_id = lambda: "alice"
    broker = NS(change_reading_preference=Mock())
    view._submit_feedback(
        broker=broker, identity_id="alice", library_key="artifact:a",
        preference="liked", revision=3, clear_all=False,
    )
    assert len(submitted) == 1
    view._generation = 11
    assert submitted[0](NS(cancelled=lambda: False)) is None
    broker.change_reading_preference.assert_not_called()
    view._feedback_write_pending = False
    view.selected_identity_id = lambda: "bob"
    view._submit_feedback(
        broker=broker, identity_id="alice", library_key="artifact:a",
        preference="liked", revision=3, clear_all=False,
    )
    assert len(submitted) == 1


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
