from cifereca.gui.borrower_information_action import advisory_text, _candidate


def test_advisory_copy_does_not_promote_permission_or_echo_secret():
    text = advisory_text({"observed_at": 1700000000, "item_offer": "unavailable",
                          "permission": "yes", "remaining_quota": 42,
                          "secret": "private-url"})
    assert "Indisponibilité observée" in text
    assert "Permission : inconnue" in text
    assert "Quota : inconnu" in text
    assert "private-url" not in text


def test_invalid_notice_and_absent_observation_are_safe():
    assert _candidate(object(), object()) is None
    assert advisory_text(None) == "Informations non vérifiées."


def test_explicit_action_no_refresh_io_duplicate_click_or_stale_publication(monkeypatch):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    import sys
    from cifereca.gui import borrower_information_action as ui

    events = []
    row = Mock()
    row.get_root.return_value = object()
    group = Mock()
    button = Mock()
    account = NS(identity_id="reader", unique_id="account")
    item = NS(source_account_id="account", metadata={})
    valid = NS(current=True)
    service = object()
    window = NS(service=service, action_button=lambda *args: button,
                runner=NS(submit=lambda *args, **kwargs: events.append((args, kwargs))))
    panel = NS(window=window, _append_content=Mock())

    def prepare(*args, **kwargs):
        events.append("prepared")
        return lambda: {"observed_at": 1700000000, "item_offer": "unavailable"}

    monkeypatch.setitem(sys.modules, "cifereca.core.borrower_information",
                        NS(prepare_borrower_information=prepare))
    monkeypatch.setitem(sys.modules, "gi.repository",
                        NS(Adw=NS(PreferencesGroup=lambda: group, ActionRow=lambda: row)))
    monkeypatch.setattr(ui, "_candidate", lambda *args: (account, lambda: valid.current))

    assert ui.add_borrower_information_action(panel, item)
    assert events == []
    invoke = button.connect.call_args.args[1]
    invoke(button)
    invoke(button)
    assert len(events) == 2 and events[0] == "prepared"
    args, kwargs = events[1]
    assert kwargs["origin"] == "user"
    result = args[1]()
    args[2](result)
    assert "Indisponibilité observée" in row.set_subtitle.call_args.args[0]

    invoke(button)
    args, _ = events[-1]
    result = args[1]()
    item.source_account_id = "other"
    row.set_subtitle.reset_mock()
    args[2](result)
    row.set_subtitle.assert_not_called()
    invoke(button)
    assert len(events) == 4
