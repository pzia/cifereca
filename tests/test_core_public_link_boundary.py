from __future__ import annotations
from tests.gui_source import gui_source_text

import ast
from pathlib import Path

import pytest

from cifereca.catalogue_link import catalogue_url, safe_document_link_url


@pytest.mark.parametrize("unsafe", [
    " https://catalogue.example/book/1",
    "https://catalogue.example/book/1 ",
    "https://catalogue.example/book/1\n",
    "https://catalogue.example/book/1\x7f",
    "https://catalogue.example/book 1",
    "https://catalogue.example/book\u00a01",
    "https://@catalogue.example/book/1",
    "https://user:@catalogue.example/book/1",
    "https://catalogue.example\\@evil.example/book/1",
    "https://catalogue.example/book/1?x=1;token=secret",
    "https://catalogue.example/book/1?%2574oken=secret",
    "https://catalogue.example/book/1?%252574oken=secret",
    "https://catalogue.example/book/1?%252525252574oken=secret",
    "https://catalogue.example/book/1#%252573ignature=secret",
    "https://catalogue.example/book/1?x%253Btoken=secret",
    "https://catalogue.example/book/1?X-Amz-Signature=secret",
    "https://catalogue.example/book/1#access_token=secret",
    "https://catalogue.example/book/1#x=1;signature=secret",
    "https://catalogue.example/download",
    "https://catalogue.example/download/book",
    "https://catalogue.example/downloads/book",
    "https://catalogue.example/Pages/Compte/MesLivres.aspx",
    "https://catalogue.example/Pages/Compte/readBook.ashx?lid=1",
    "https://catalogue.example/fulfillment/book.acsm",
    "https://catalogue.example/book%252Eepub",
])
def test_untrusted_public_links_are_refused_by_shared_url_validator(unsafe):
    assert catalogue_url({"friendly_url": unsafe}) is None
    assert safe_document_link_url(unsafe, relation="notice") is None


def test_safe_public_book_notice_keeps_legitimate_query_and_anchor():
    uri = "https://catalogue.example/notice/42?ref=library#section-2"
    assert catalogue_url({"friendly_url": uri}) == uri
    assert safe_document_link_url(uri, relation="notice") == uri


def test_catalogue_root_is_allowed_only_for_generic_links():
    uri = "https://catalogue.example/"
    assert safe_document_link_url(uri, relation="notice") is None
    assert safe_document_link_url(uri, relation="catalogue") == uri


def test_candidate_fallback_keeps_next_attested_public_url():
    row = {
        "friendly_url": "https://catalogue.example/loan/secure",
        "metadata": {"search_result": {"friendly_url": "https://catalogue.example/notice/42"}},
    }
    assert catalogue_url(row) == "https://catalogue.example/notice/42"


@pytest.mark.parametrize("metadata", [None, [], "bad metadata", {"search_result": []}])
def test_untrusted_metadata_shapes_do_not_crash_public_link_projection(metadata):
    assert catalogue_url({"metadata": metadata, "friendly_url": "https://catalogue.example/book/1"}) == (
        "https://catalogue.example/book/1"
    )


def test_detail_renderer_applies_validation_to_direct_plugin_links():
    main = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    module = ast.parse(gui_source_text(main))
    detail = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    renderer = next(node for node in detail.body if isinstance(node, ast.FunctionDef) and node.name == "show_presentation")
    source = ast.unparse(renderer)
    assert "safe_document_link_url(link.url, relation=link.relation)" in source
    assert "if not uri or uri in self._rendered_document_urls:" in source
    assert "Gtk.LinkButton(uri=uri, label=str(link.label))" in source


def test_plugin_master_detail_uri_action_is_checked_and_invalid_actions_are_dropped():
    main = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    module = ast.parse(gui_source_text(main))
    cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "PublicBookMasterDetailView")
    renderer = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "show_detail")
    source = ast.unparse(renderer)
    assert "safe_document_link_url(raw_uri, relation='related')" in source
    assert "if not uri:" in source
    assert "continue" in source
    assert "Gtk.LinkButton(uri=uri, label=str(action.title))" in source


def test_native_document_link_rechecks_context_before_gtk_default_launch():
    from types import SimpleNamespace as NS

    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    cls = next(node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "show_presentation")

    class LinkButton:
        def __init__(self, *, uri, label):
            self.uri, self.label, self.mapped = uri, label, True
            self.opened = []

        def set_tooltip_text(self, _text):
            pass

        def get_mapped(self):
            return self.mapped

        def connect(self, signal, callback):
            assert signal == "activate-link"
            self.callback = callback

        def activate(self):
            if not self.callback(self):
                self.opened.append(self.uri)

    namespace = {
        "current_detail_trace": lambda: None,
        "clear_box": lambda _content: None,
        "Gtk": NS(LinkButton=LinkButton),
        "safe_document_link_url": safe_document_link_url,
        "UiBookDetailPresentation": object,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[method], type_ignores=[])), str(source), "exec"), namespace)
    render = namespace["show_presentation"]
    service = NS(current_identity=NS(identity_id="alice"),
                 _account_context_epoch=1, _source_context_epoch=1)
    window = NS(service=service, _route_display_token=object())
    buttons = []

    def clear():
        for button in buttons:
            button._cifereca_action_context_revoked = True
        buttons.clear()

    panel = NS(window=window, content=object(),
               _cancel_meta_request=lambda: None,
               _focused_action_key=lambda: "",
               _clear_actions=clear,
               _cover_and_title=lambda *_a, **_kw: None,
               _show_description=lambda *_: None,
               _actions=buttons.extend)
    url = "https://catalogue.example/notice/42"
    presentation = NS(title="Livre", subtitle="", thumbnail_url=None, badges=(),
                      sections=(), links=(NS(id="notice", label="Fiche",
                                             url=url, relation="notice"),))

    for invalidate in (
        lambda button: setattr(service, "_account_context_epoch", service._account_context_epoch + 2),
        lambda button: setattr(service, "_source_context_epoch", service._source_context_epoch + 2),
        lambda button: setattr(window, "_route_display_token", object()),
        lambda button: setattr(service, "current_identity", NS(identity_id="alice")),
        lambda button: setattr(button, "mapped", False),
        lambda button: setattr(button, "_cifereca_action_context_revoked", True),
    ):
        render(panel, presentation)
        button, = buttons
        button.activate()
        assert button.opened == [url]
        invalidate(button)
        button.activate()
        assert button.opened == [url]

    render(panel, presentation)
    original, = buttons
    render(panel, presentation)
    original.activate()
    assert original.opened == []
    current, = buttons
    current.activate()
    assert current.opened == [url]
    window._closed = True
    current.activate()
    assert current.opened == [url]
