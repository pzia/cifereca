"""#3105: real Synchronisation GTK handler rendered with offline widget doubles."""
from types import SimpleNamespace as NS

from tests import test_core_ui_1347_sync_action_scope as harness


def _window(*, provider_status="ready", enabled=True, config_enabled=True):
    w, broker, calls, _build, _page = harness._fixture(sources=[])
    w.service.config = NS(provider_accounts=lambda provider: [
        {
            "provider": "paris", "id": "secret-login",
            "identity_id": "alice", "enabled": config_enabled,
            "label": "PRIVATE CARD", "credentials": {"password": "PRIVATE PASSWORD"},
        }
    ])
    w.service.registry = NS(
        maybe_get=lambda name: None if provider_status is None
        else NS(descriptor=NS(state=provider_status)),
    )
    w.service._provider_enabled_for_identity = lambda pid, iid: enabled
    w.service.accounts_for_identity = lambda identity: []
    w.connect_configured_sources = lambda: calls.append(("user-reconnect",))
    w._refresh_sync_page()
    return w, broker, calls


def test_paris_offline_card_visible_alongside_no_live_sources_and_action_guarded():
    w, _broker, calls = _window()
    group = w._sync_sources_box.children[0]
    row = group.children[0]
    assert "Bibliothèques de Paris" in row.title
    assert "connexion non établie" in row.subtitle
    assert "PRIVATE" not in repr((row.title, row.subtitle))
    assert not w._sync_all_button.sensitive
    assert "1 connexion(s) Paris configurée(s)" in w._sync_summary.label
    assert not w.runner.tasks
    retry, = row.suffixes
    assert retry.label == "Reconnecter toutes les sources"
    retry.click()
    assert calls[-1] == ("user-reconnect",)
    assert not retry.sensitive
    assert not w.runner.tasks


def test_disabled_or_missing_paris_keeps_card_but_cannot_reconnect():
    for status, enabled, config_enabled, expected in (
        (None, True, True, "absent"),
        ("ready", False, True, "désactivé"),
        ("ready", True, False, "désactivé"),
    ):
        w, _broker, calls = _window(
            provider_status=status, enabled=enabled, config_enabled=config_enabled,
        )
        row = w._sync_sources_box.children[0].children[0]
        assert expected in row.subtitle
        assert row.suffixes == []
        assert not w._sync_all_button.sensitive
        assert not w.runner.tasks
        assert not any(x[0] == "user-reconnect" for x in calls)


def test_other_identity_hides_paris_and_invalidates_old_reconnect_button():
    w, broker, calls = _window()
    old_retry = w._sync_sources_box.children[0].children[0].suffixes[0]
    w.service.current_identity = NS(identity_id="bob", label="Bob")
    w._refresh_sync_page()
    assert not any(
        "Bibliothèques de Paris" in str(getattr(child, "title", ""))
        for group in w._sync_sources_box.children
        for child in getattr(group, "children", ())
    )
    old_retry.click()
    assert ("user-reconnect",) not in calls
    assert not w.runner.tasks
