"""#2141: fixed sync context and source-alert totals without provider data."""
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def test_header_and_global_actions_stay_outside_scroll_after_source_refresh():
    window, _, calls, build, page = _fixture(sources=[
        _source(source_id="one", label="Kobo un"),
        _source(source_id="two", label="Kobo deux"),
    ])
    # The first root child is the fixed header; the scrolling list is second.
    assert len(page.children) == 2
    fixed_header, scrolling_list = page.children
    assert scrolling_list.child is not None
    assert window._sync_sources_box in scrolling_list.child.children
    assert window._sync_summary in fixed_header.children
    assert window._sync_attention_row in fixed_header.children
    assert window._sync_attention_row.get_visible() is False
    assert window._sync_all_button.sensitive
    assert not window.runner.tasks
    assert all(call[0] == "sources" for call in calls)
    window._refresh_sync_page()
    assert len(page.children) == 2
    assert window._sync_summary in fixed_header.children
    assert window._sync_sources_box in scrolling_list.child.children
    assert not window.runner.tasks


def test_aggregated_attention_counts_are_closed_and_update_on_refresh():
    bad = _source(provider_id="paris", source_id="bad", status="failed",
                  error="https://private.invalid/?token=SECRET_2141")
    warning = _source(source_id="offline", enabled=True, connected=False)
    hidden = _source(provider_id="gutenberg", source_id="public",
                     sync_capable=False, status="failed", error="SECRET_2141")
    window, _, calls, _, _ = _fixture(sources=[
        bad, warning, hidden, _source(source_id="healthy"),
    ])
    assert window._sync_attention_row.get_visible() is True
    assert window._sync_attention_text.label == (
        "À vérifier : 1 échec(s) · 1 avertissement(s). "
        "Consultez les comptes signalés ci-dessous."
    )
    assert window._sync_attention_icon.icon_name == "dialog-error-symbolic"
    assert "4 source(s)" in window._sync_summary.label
    assert "SECRET_2141" not in window._sync_attention_text.label
    assert not window.runner.tasks
    bad.status, bad.error = "success", ""
    window._refresh_sync_page()
    assert window._sync_attention_text.label.startswith("À vérifier : 0 échec(s) · 1")
    assert window._sync_attention_icon.icon_name == "dialog-warning-symbolic"
    warning.connected = True
    window._refresh_sync_page()
    assert window._sync_attention_row.get_visible() is False
    assert not window.runner.tasks
    assert all(call[0] == "sources" for call in calls)


def test_no_identity_keeps_attention_hidden_and_global_sync_disabled():
    window, _, calls, _, page = _fixture(
        identity_id=None,
        sources=[_source(status="failed", error="PRIVATE_DATA")],
    )
    assert window._sync_attention_row.get_visible() is False
    assert not window._sync_all_button.sensitive
    assert not calls
    assert page.children[1].child is not None
