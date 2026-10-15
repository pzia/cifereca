from __future__ import annotations

import ast
from pathlib import Path

from cifereca.plugin_api import (
    BookRef,
    BookReferenceRecommendationService,
    contract_definition,
)


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"


class _Recommendation:
    def recommend_from_book(self, book: BookRef) -> str:
        return "custom-recommendations"


class _BadRecommendation:
    pass


def _method(name: str) -> str:
    if name == "_book_action_context":
        owner = ast.parse(MAIN.with_name("book_action_context_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in owner.body if isinstance(n, ast.FunctionDef)
                                and n.name == "book_action_context"))
    if name == "_library_selected":
        tree = ast.parse(MAIN.with_name("library_detail_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                                and n.name == "library_selected"))
    if name == "_book_recommendation_target":
        tree = ast.parse(MAIN.with_name("recommendation_target_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                                and n.name == "book_recommendation_target"))
    if name == "_add_book_recommendation_action":
        tree = ast.parse(MAIN.with_name("book_recommendation_controller.py").read_text(encoding="utf-8"))
        return ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                                and n.name == "add_book_recommendation_action"))
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_book_reference_recommendation_contract_is_public_and_structural():
    assert isinstance(_Recommendation(), BookReferenceRecommendationService)
    assert not isinstance(_BadRecommendation(), BookReferenceRecommendationService)
    definition = contract_definition("recommendation")
    assert definition is not None
    assert "recommendation.book-reference" in definition.capabilities


def test_book_ref_can_carry_available_metadata_without_losing_existing_fields():
    book = BookRef(
        "book-1",
        title="Titre",
        authors=("Auteur",),
        identifiers={"provider": "kobo"},
        metadata={"language": "fr", "format": "epub"},
    )
    assert book.title == "Titre"
    assert book.authors == ("Auteur",)
    assert book.metadata["language"] == "fr"


def test_library_action_context_forwards_item_metadata_into_book_ref():
    source = _method("_book_action_context")
    assert "book_ref_from_row(item)" in source
    assert "metadata = dict(item.get('metadata') or {})" in source
    assert "book=book" in source


def test_recommendation_action_requires_active_capability_and_public_protocol():
    source = _method("_book_recommendation_target")
    assert "manager.services('recommendation')" in source
    assert "'recommendation.book-reference' not in set(resolved.capabilities)" in source
    assert "isinstance(service, BookReferenceRecommendationService)" in source


def test_recommendation_callback_uses_plugin_route_not_concrete_route_name():
    source = _method("_add_book_recommendation_action")
    assert "Recommander à partir de ce livre" in source
    assert "service.recommend_from_book(book)" in source
    assert "window.navigate(route)" in source
    assert "navigate('recommendations')" not in source


def test_library_detail_adds_recommendation_action_for_normal_and_loan_rows():
    source = _method("_library_selected")
    assert source.count("_add_book_recommendation_action") == 2
