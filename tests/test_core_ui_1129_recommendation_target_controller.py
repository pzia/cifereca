"""CoreUI P0 #1129: public protocol recommendation routing and stale context."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.recommendation_target_controller import book_recommendation_target
from cifereca.gui.book_recommendation_controller import add_book_recommendation_action
from cifereca.plugin_api import BookRef, BookReferenceRecommendationService


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "recommendation_target_controller.py"


class Recommendation:
    def __init__(self, route="plugin/recommendations/new"):
        self.route = route
        self.calls = []

    def recommend_from_book(self, book):
        self.calls.append(book)
        return self.route


class Button:
    def __init__(self):
        self.handlers = {}

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def click(self):
        self.handlers["clicked"](self)


def _resolved(service, capabilities=("recommendation.book-reference",)):
    return NS(service=service, capabilities=capabilities)


def _host(*, service=None, rows=None, book=None):
    events = []
    ref = book if book is not None else BookRef("book-1", title="Livre")
    recommendation = service if service is not None else Recommendation()
    resolved = rows if rows is not None else [_resolved(recommendation)]
    manager = NS(services=lambda category: (
        events.append(("services", category)) or resolved
    ))
    window = NS(
        _closed=False,
        _identity_context_generation=3,
        _account_context_generation=5,
        plugin_manager=manager,
        _book_action_context=lambda item: (
            events.append(("book-context", item)) or NS(book=ref)
        ),
        action_button=lambda *args: Button(),
        toast=lambda message: events.append(("toast", message)),
        show_error=lambda message, title: events.append(("error", message, title)),
        navigate=lambda route: events.append(("navigate", route)),
    )
    window._book_recommendation_target = lambda item: book_recommendation_target(
        window, item,
        BookReferenceRecommendationService=BookReferenceRecommendationService,
    )
    actions = []
    detail = NS(add_actions=lambda buttons: actions.extend(buttons))
    return window, detail, recommendation, ref, events, actions


def test_controller_headless_and_facade_injects_public_protocol():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(x) for x in ast.walk(module)
               if isinstance(x, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in imp or "main_window" in imp for imp in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(x for x in main.body if isinstance(x, ast.ClassDef) and x.name == "MainWindow")
    facade = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
                  and x.name == "_book_recommendation_target")
    assert [a.arg for a in facade.args.args] == ["self", "item"]
    rendered = ast.unparse(facade)
    assert "recommendation_target_controller.book_recommendation_target" in rendered
    assert "BookReferenceRecommendationService=BookReferenceRecommendationService" in rendered


def test_declared_capability_and_structural_public_protocol_yield_exact_book_ref():
    window, detail, service, book, events, actions = _host()
    target = window._book_recommendation_target({"title": "Livre"})
    assert target == (service, book)
    assert target[1] is book
    assert events == [
        ("book-context", {"title": "Livre"}),
        ("services", "recommendation"),
    ]


def test_missing_manager_does_not_request_book_context():
    window, detail, service, book, events, actions = _host()
    window.plugin_manager = None
    assert window._book_recommendation_target({}) is None
    assert events == []


def test_missing_book_does_not_query_plugins():
    window, detail, service, book, events, actions = _host()
    window._book_action_context = lambda item: NS(book=None)
    assert window._book_recommendation_target({}) is None
    assert events == []


def test_closed_window_does_not_read_manager_or_service():
    window = NS(_closed=True)
    assert book_recommendation_target(
        window, object(),
        BookReferenceRecommendationService=BookReferenceRecommendationService,
    ) is None


def test_service_discovery_exception_returns_none_without_crashing_details():
    window, detail, service, book, events, actions = _host()
    window.plugin_manager.services = lambda _: (
        (_ for _ in ()).throw(ValueError("plugin offline"))
    )
    assert window._book_recommendation_target({}) is None
    assert actions == []


def test_unadvertised_capability_never_exposes_recommendation_button():
    window, detail, service, book, events, actions = _host(
        rows=[_resolved(Recommendation(), capabilities=("recommendation",))]
    )
    add_book_recommendation_action(window, detail, {"title": "Livre"})
    assert actions == []


def test_declared_capability_without_public_protocol_is_not_usable():
    window, detail, service, book, events, actions = _host(
        rows=[_resolved(object())]
    )
    assert window._book_recommendation_target({}) is None


def test_first_compatible_service_is_selected_after_skipping_invalid_services():
    first = Recommendation("private-first")
    second = Recommendation("private-second")
    window, detail, service, book, events, actions = _host(rows=[
        _resolved(first, capabilities=("legacy",)),
        _resolved(object()),
        _resolved(second),
    ])
    chosen = window._book_recommendation_target({})
    assert chosen == (second, book)


def test_action_navigates_only_to_service_declared_route():
    window, detail, service, book, events, actions = _host()
    add_book_recommendation_action(window, detail, {})
    assert len(actions) == 1
    assert actions[0]._cifereca_action_id == "recommend-from-book"
    assert actions[0]._cifereca_action_priority == 25
    actions[0].click()
    assert service.calls == [book]
    assert events[-1] == ("navigate", "plugin/recommendations/new")


@pytest.mark.parametrize("field", ["_identity_context_generation", "_account_context_generation"])
def test_old_recommendation_button_is_not_active_in_another_scope(field):
    window, detail, service, book, events, actions = _host()
    add_book_recommendation_action(window, detail, {})
    before = len(events)
    setattr(window, field, getattr(window, field) + 1)
    actions[0].click()
    assert service.calls == []
    assert len(events) == before


def test_action_callback_after_close_is_inert():
    window, detail, service, book, events, actions = _host()
    add_book_recommendation_action(window, detail, {})
    window._closed = True
    before = len(events)
    actions[0].click()
    assert service.calls == []
    assert len(events) == before


def test_service_that_closes_window_cannot_navigate_after_returning():
    window, detail, service, book, events, actions = _host()
    def close_and_return(book):
        window._closed = True
        return "plugin/private-route"
    service.recommend_from_book = close_and_return
    add_book_recommendation_action(window, detail, {})
    actions[0].click()
    assert not any(row[0] == "navigate" for row in events)


def test_failure_after_teardown_does_not_open_error_dialog():
    window, detail, service, book, events, actions = _host()
    def close_and_fail(book):
        window._closed = True
        raise RuntimeError("private error")
    service.recommend_from_book = close_and_fail
    add_book_recommendation_action(window, detail, {})
    actions[0].click()
    assert not any(row[0] == "error" for row in events)


def test_missing_route_preserves_public_error_without_navigation():
    window, detail, service, book, events, actions = _host(
        service=Recommendation(route="")
    )
    add_book_recommendation_action(window, detail, {})
    actions[0].click()
    assert events[-1] == (
        "error", "Le service n’a fourni aucune page à ouvrir.",
        "Recommandation impossible",
    )
    assert not any(row[0] == "navigate" for row in events)
