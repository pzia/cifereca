"""#7161: Library all-profiles is read-only, never a borrower/profile switch.

Headless GTK, synthetic Core reads and temporary SQLite: no ADEPT subprocess,
provider request, loan creation, metadata reset or publication.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.library_page_controller import build_library_page
from cifereca.storage import Storage
from tests.test_core_ui_1129_library_page_controller import Widget, _gtk, _page
from tests.test_core_ui_identity_async_recent import _harness, _change_identity


def test_identity_aba_and_all_profiles_toggle_keep_exact_library_owner(tmp_path):
    host = _harness()
    host._closed = False
    host._render_library = Mock()
    host._library_search_online = Mock()
    host._library_selected = Mock()
    host.open_library_folder = Mock()
    host.choose_acsm = Mock()
    host.navigate = Mock()
    controls = NS(entry=object(), primary=Widget())
    host._query_controls = lambda *_args, **_kwargs: controls

    db = Storage(tmp_path / "state.sqlite")
    try:
        db.adept_profile_binding_set("identity", "alice", "stable-adobe-a")
        db.adept_profile_binding_set("identity", "bob", "stable-adobe-b")
        host.service.storage = db
        host.service.kobo_profiles = Mock(side_effect=AssertionError(
            "Library projection must never enumerate ADEPT profiles"
        ))
        host.service.catalog_scan = Mock(side_effect=AssertionError(
            "Library projection must not scan local files"
        ))
        host.service.library_entries_all_profiles = Mock(return_value=[
            {"identity_id": "alice", "entry": {
                "key": "a", "identity_id": "alice", "title": "Alice",
            }},
            {"identity_id": "bob", "entry": {
                "key": "b", "identity_id": "bob", "title": "Bob",
            }},
        ])
        build_library_page(host, Gtk=_gtk(), MasterDetailPage=_page)
        scope_button = controls.primary.children[1]
        assert host._library_all_profiles is False

        host.refresh_library(False)
        old_alice = host.runner.requests[-1]
        _change_identity(host, "bob")
        scope_button.click()
        assert host._library_all_profiles is True
        assert host.service.current_identity.identity_id == "bob"
        assert scope_button.label == "Profil courant"
        global_bob = host.runner.requests[-1]
        global_entries = global_bob.work()
        assert {x["identity_id"] for x in global_entries} == {"alice", "bob"}
        assert all(x["_library_view_owner"] == x["identity_id"] for x in global_entries)
        # The click changed only the Library read scope, not Core authority.
        assert db.adept_profile_binding_get("identity", "bob") == "stable-adobe-b"

        _change_identity(host, "alice")
        scope_button.click()
        assert host._library_all_profiles is False
        assert host.service.current_identity.identity_id == "alice"
        assert scope_button.label == "Tous les profils"
        fresh_alice = host.runner.requests[-1]

        # A/B/A alone cannot resurrect old GTK callbacks or a global view.
        old_alice.ok(old_alice.work())
        global_bob.ok(global_entries)
        assert host.library_shown == []
        assert fresh_alice.work() == [{"identity_id": "alice"}]
        fresh_alice.ok(fresh_alice.work())
        assert host.library_shown == [[{"identity_id": "alice"}]]
        assert host._library_scope_generation == 2
        assert db.adept_profile_binding_get("identity", "alice") == "stable-adobe-a"
        assert db.adept_profile_binding_get("identity", "bob") == "stable-adobe-b"
        host.service.kobo_profiles.assert_not_called()
        host.service.catalog_scan.assert_not_called()
        host.service.library_entries_all_profiles.assert_called_once_with()
    finally:
        db.close()
