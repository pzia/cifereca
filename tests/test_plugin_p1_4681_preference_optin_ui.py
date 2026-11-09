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
