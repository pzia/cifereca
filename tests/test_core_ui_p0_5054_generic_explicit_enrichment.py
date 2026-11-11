"""Headless behavioral checks of generic explicit metadata UI (P0 #5054).

The function is AST-extracted to avoid importing GTK during Core tests.
The production body is compiled unchanged; only host dependencies are faked.
"""
import ast
import __future__
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.models import SearchResult, ItemDetail


def _action():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_details.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_add_explicit_enrichment_action")
    namespace = {
        "__package__": "cifereca.gui",
        "TaskPriority": NS(USER_ACTION="user_action"),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace["_add_explicit_enrichment_action"]


def _item(**changes):
    values = dict(
        id="book", record_id="fixture:book", docbase="SYNTHETIC",
        medium="digital", title="Book", author=None, type="ebook",
        format=None, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url="https://fictional.invalid/books/42",
        scenario="SYNTHETIC", source_provider="future-provider",
        source_credential="reader", source_collection="library",
        owned=True, downloadable=True, download_url=None,
    )
    values.update(changes)
    return SearchResult(**values)


def _detail():
    return ItemDetail(
        record_id="fixture:book", docbase="SYNTHETIC", title="Book",
        author=None, type="ebook", year=None, thumbnail_url=None,
        availability_status="owned", availability_label="Owned", holdings=[],
        source_provider="future-provider", source_credential="reader",
    )


class Button:
    def __init__(self):
        self.callback = None
    def connect(self, event, callback):
        assert event == "clicked"
        self.callback = callback
    def click(self):
        self.callback(self)


class Host:
    def __init__(self, row=None, *, public=False, abi=True):
        self.row = row or _item()
        self.context = (3, 4, 5, "identity-A")
        self.active = True
        self.actions, self.tasks, self.refresh, self.toasts = [], [], [], []
        self.requests = 0
        self.search_objects = {"source:book": self.row}
        self.button = Button()
        self.search_page = NS(detail=NS(
            window=NS(action_button=lambda *_args: self.button),
            add_actions=self.actions.extend,
        ))
        method = "enrich_public_detail" if public else "enrich_owned_public_detail"
        session = NS(**{method: lambda row: row}) if abi else NS()
        self.service = NS(
            _sessions={f"{self.row.source_provider}:{self.row.source_credential}": session},
            _session_key=lambda provider, credential: f"{provider}:{credential}",
            enrich_provider_search_result=self.enrich,
        )
        self.runner = NS(submit=lambda *a, **kw: self.tasks.append((a, kw)))
    def _search_detail_context_current(self, key, context):
        return self.active and key == "book" and context == self.context
    def _apply_search_detail(self, *a, **kw):
        self.refresh.append((a, kw))
    def toast(self, value, *_args):
        self.toasts.append(value)
    def enrich(self, row, *, expected_identity_id):
        assert expected_identity_id == "identity-A"
        self.requests += 1
        return replace(row, author="Public metadata")


def test_generic_action_is_explicit_and_preserves_original_row_key():
    host = Host()
    assert _action()(host, "book", host.row, _detail(), host.context)
    assert len(host.actions) == 1
    assert host.requests == 0 and host.tasks == []
    host.button.click()
    args, kwargs = host.tasks[0]
    assert kwargs["origin"] == "user" and kwargs["kind"] == "plugin"
    assert kwargs["control"] is host.button
    args[2](args[1]())
    assert host.requests == 1
    assert list(host.search_objects) == ["source:book"]
    assert host.search_objects["source:book"].author == "Public metadata"
    assert host.refresh[0][1]["context"] == host.context


def test_missing_abi_never_offers_action_or_submits_request():
    host = Host(abi=False)
    assert not _action()(host, "book", host.row, _detail(), host.context)
    assert host.actions == [] and host.tasks == []


def test_public_provider_uses_current_public_abi_without_private_account():
    row = _item(source_provider="future-public", source_credential="__public__",
                source_collection="public_catalog", owned=False, downloadable=False)
    host = Host(row, public=True)
    assert _action()(host, "book", row, _detail(), host.context)
    assert host.requests == 0


def test_replaced_row_cannot_publish_stale_result():
    host = Host()
    _action()(host, "book", host.row, _detail(), host.context)
    host.button.click()
    args, _ = host.tasks[0]
    host.search_objects["source:book"] = replace(host.row, title="New selection")
    args[2](replace(host.row, author="Stale"))
    assert host.refresh == [] and host.search_objects["source:book"].author is None


def test_identity_switch_refuses_click_and_failure_text_is_redacted():
    host = Host()
    _action()(host, "book", host.row, _detail(), host.context)
    host.active = False
    host.button.click()
    assert host.tasks == []
    host.active = True
    host.button.click()
    args, _ = host.tasks[0]
    args[3](RuntimeError("https://private.invalid/?token=SECRET"))
    assert host.toasts == ["Enrichissement indisponible ou non attesté."]
    host.active = False
    args[2](replace(host.row, author="Stale"))
    assert host.refresh == []
