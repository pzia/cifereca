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
