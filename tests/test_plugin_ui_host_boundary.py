"""Public host forwarding using the actual declaration with widget factories stubbed."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.plugin_api import BookRef, UiActionContext, UiIdentityHost


ROOT = Path(__file__).resolve().parents[1]


def host_type(**factories):
    path = ROOT / 'src/cifereca/gui/plugin_ui_host.py'
    tree = ast.parse(path.read_text())
    declaration = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_UiHost')
    future = ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    env = dict(BookRef=BookRef, **factories)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future,declaration],type_ignores=[])),str(path),'exec'),env)
    return env['_UiHost']


def test_main_window_keeps_real_owner_as_compatibility_class_attribute():
    tree = ast.parse((ROOT/'src/cifereca/gui/main_window.py').read_text())
    imported = next(n for n in tree.body if isinstance(n,ast.ImportFrom) and n.module=='plugin_ui_host')
    assert imported.level == 1 and [n.name for n in imported.names]==['_UiHost']
    window = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
    alias = next(n for n in window.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_UiHost' for t in n.targets))
    assert isinstance(alias.value,ast.Name) and alias.value.id=='_UiHost'
    owner = ast.parse((ROOT/'src/cifereca/gui/plugin_ui_host.py').read_text())
    guard = next(n for n in owner.body if isinstance(n,ast.If))
    assert isinstance(guard.test,ast.Name) and guard.test.id=='TYPE_CHECKING'
    assert any(isinstance(n,ast.ImportFrom) and n.module=='main_window' for n in guard.body)


def test_supplied_book_context_is_retained_when_window_route_changes():
    book = BookRef('book-one')
    context = UiActionContext(target='book',route='library',book=book)
    window = NS(view_stack=NS(get_visible_child_name=lambda:'search'))
    host = host_type()(window, context)
    assert host.action_context() is context and host.current_book() is book
    assert isinstance(host,UiIdentityHost)


def test_subscription_cancellation_is_idempotent_and_keeps_other_listeners():
    window = NS()
    host = host_type()(window)
    first, second = Mock(), Mock()
    cancel = host.subscribe_identity_overview(first)
    host.subscribe_identity_overview(second)
    assert window._overview_listeners == [first, second]
    cancel(); cancel()
    assert window._overview_listeners == [second]


def test_each_action_button_retains_its_own_contribution_and_host():
    buttons, delivered = [], []
    class Button:
        def connect(self,_signal,callback): self.callback=callback
    class Bar:
        def __init__(self): self.children=[]
        def append(self,button): self.children.append(button)
    def button_factory(*args):
        button=Button(); buttons.append(button); return button
    host=host_type(AdaptiveActionBar=Bar,action_button=button_factory)(NS())
    actions=[NS(title=str(i),icon_name='',callback=lambda owner,index=i:delivered.append((index,owner))) for i in range(3)]
    bar=host.build_action_bar(actions)
    assert bar.children==buttons
    for button in buttons: button.callback(button)
    assert delivered==[(0,host),(1,host),(2,host)]


def test_identity_overview_is_copied_and_library_refresh_never_searches_online():
    data=[{'identity_id':'alice','nested':{'count':1}}]
    window=NS(service=NS(dashboard_state=lambda:data),refresh_library=Mock(),refresh_recent=Mock(),run_search=Mock())
    host=host_type()(window,UiActionContext(target='page',route='library'))
    copy=host.identity_overview(); copy[0]['nested']['count']=99
    assert data[0]['nested']['count']==1
    host.refresh_context()
    window.refresh_library.assert_called_once_with(False)
    window.refresh_recent.assert_called_once_with()
    window.run_search.assert_not_called()


def test_closed_plugin_host_refuses_all_owner_mutations_and_live_service_reads():
    window = NS(
        _closed=True,
        navigate=Mock(), open_book_detail=Mock(return_value=True),
        search_books=Mock(return_value=True), choose_acsm=Mock(),
        open_identity_library=Mock(), open_identity_view=Mock(),
        run_opening_sync=Mock(), refresh_library=Mock(),
        refresh_recent=Mock(), run_search=Mock(),
        service=NS(dashboard_state=Mock()),
        plugin_manager=NS(diagnostic=Mock()),
        plugins_page=NS(focus_plugin=Mock()),
    )
    host = host_type()(window, UiActionContext(target="page", route="library"))
    # Plugin error notifications must not reach a detached GTK toast widget.
    window.toast = Mock()
    host.toast("private provider failure")
    window.toast.assert_not_called()
    assert host.open_book_detail(BookRef("one")) is False
    assert host.search_books("Loup") is False
    host.navigate("search")
    host.open_plugin_settings("provider-private")
    host.open_identity_library("alice")
    host.open_identity_view("alice", route="library")
    host.refresh_context()
    host.refresh_detail()
    host.import_acsm()
    host.synchronize()
    assert host.identity_overview() == []
    assert host.plugin_statuses() == []
    for method in (
        window.navigate, window.open_book_detail, window.search_books,
        window.choose_acsm, window.open_identity_library,
        window.open_identity_view, window.run_opening_sync,
        window.refresh_library, window.refresh_recent, window.run_search,
        window.service.dashboard_state, window.plugin_manager.diagnostic,
        window.plugins_page.focus_plugin,
    ):
        method.assert_not_called()


def test_closed_plugin_host_cannot_resubscribe_identity_overview():
    window = NS(_closed=True)
    host = host_type()(window)
    callback = Mock()
    cancel = host.subscribe_identity_overview(callback)
    assert callable(cancel)
    cancel()
    assert not hasattr(window, "_overview_listeners")
    callback.assert_not_called()


def test_action_button_callback_is_revoked_when_owner_closes():
    class Button:
        def connect(self, _event, callback):
            self.callback = callback
    class Bar:
        def __init__(self):
            self.children = []
        def append(self, item):
            self.children.append(item)
    window = NS(_closed=False)
    host = host_type(
        AdaptiveActionBar=Bar, action_button=lambda *args: Button(),
    )(window)
    fired = []
    bar = host.build_action_bar([
        NS(title="Rapport", icon_name="document-open",
           callback=lambda caller: fired.append(caller)),
    ])
    bar.children[0].callback(bar.children[0])
    assert fired == [host]
    window._closed = True
    bar.children[0].callback(bar.children[0])
    assert fired == [host]


def test_live_plugin_host_still_routes_navigation_and_sync():
    actions = []
    window = NS(
        _closed=False,
        navigate=lambda route: actions.append(("navigate", route)),
        plugins_page=NS(focus_plugin=lambda p: actions.append(("focus", p))),
        open_identity_view=lambda identity, **kwargs: actions.append(
            ("identity-view", identity, kwargs)),
        run_opening_sync=lambda control, *, deep: actions.append(
            ("sync", control, deep)),
    )
    host = host_type()(window)
    host.open_plugin_settings("reader")
    host.open_identity_view("alice", route="library", providers=("local",))
    host.synchronize(deep=False)
    assert actions == [
        ("navigate", "plugins"), ("focus", "reader"),
        ("identity-view", "alice", {"route": "library", "providers": ("local",)}),
        ("sync", None, False),
    ]



def test_optional_search_provider_scope_preserves_legacy_host_signature():
    """No providers kwarg for legacy callbacks; opt-in is still forwarded."""
    calls = []

    def legacy(query, *, field, online, identity_id):
        calls.append((query, field, online, identity_id))
        return True

    window = NS(_closed=False, search_books=legacy)
    host = host_type()(window)
    assert host.search_books(
        "9780306406157", field="isbn", online=False, identity_id="alice",
    ) is True
    assert calls == [("9780306406157", "isbn", False, "alice")]

    def scoped(query, *, field, online, identity_id, providers):
        calls.append((query, field, online, identity_id, providers))
        return True

    window.search_books = scoped
    assert host.search_books(
        "9780140328721", field="isbn", online=False, identity_id="alice",
        providers=("paris",),
    ) is True
    assert calls[-1] == (
        "9780140328721", "isbn", False, "alice", ("paris",),
    )
