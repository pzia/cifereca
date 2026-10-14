"""P0 #1130: public book action contexts retain provenance and local artifact facts."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.book_action_context_controller import book_action_context
from cifereca.plugin_api import (
    ArtifactFormat, ArtifactRef, UiActionContext, book_ref_from_row,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "book_action_context_controller.py"
MAIN = ROOT / "main_window.py"


def _build(item, *, route="library", storage=None):
    if storage is None:
        storage = NS(artifacts=lambda: ())
    window = NS(
        service=NS(storage=storage),
        view_stack=NS(get_visible_child_name=lambda: route),
    )
    return book_action_context(
        window, item, Path=Path, ArtifactRef=ArtifactRef,
        ArtifactFormat=ArtifactFormat, UiActionContext=UiActionContext,
        book_ref_from_row=book_ref_from_row,
    )


def test_gtk_free_owner_and_original_mainwindow_facade():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(owner)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(x == "import gi" or x.startswith("from gi ")
                   or "main_window" in x for x in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    window = next(node for node in main.body
                  if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    facade = next(node for node in window.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_book_action_context")
    assert [arg.arg for arg in facade.args.args] == ["self", "item"]
    source = ast.unparse(facade)
    assert "book_action_context_controller.book_action_context" in source
    for name in ("Path", "ArtifactRef", "ArtifactFormat", "UiActionContext",
                 "book_ref_from_row"):
        assert name + "=" + name in source


def test_book_context_is_public_and_copies_input_metadata():
    metadata = {"description": "Résumé", "language": "fr"}
    item = {
        "item_id": "book-10", "key": "artifact-fallback",
        "title": "Livre", "author": "Alice; Bob",
        "provider_id": "paris", "metadata": metadata,
    }
    context = _build(item, storage=NS(artifacts=lambda: (_ for _ in ()).throw(
        AssertionError("no artifact means no local storage lookup")
    )))
    assert isinstance(context, UiActionContext)
    assert context.target == "book.detail"
    assert context.route == "library"
    assert context.book.book_id == "book-10"
    assert context.book.title == "Livre"
    assert context.book.authors == ("Alice", "Bob")
    assert context.book.identifiers["provider"] == "paris"
    assert context.book.identifiers["source_item_id"] == "book-10"
    assert context.artifact is None
    assert context.metadata == metadata
    assert context.metadata is not metadata
    metadata["description"] = "Titre changé"
    assert context.metadata["description"] == "Résumé"


def test_artifact_details_resolve_exact_id_and_local_epub(tmp_path):
    local = tmp_path / "original.epub"
    local.write_bytes(b"EPUB-TINY")
    calls = []
    def artifacts():
        calls.append("artifacts")
        return (
            {"artifact_id": "other", "sha256": "wrong"},
            {"artifact_id": "artifact-a", "sha256": "correct-sha"},
            {"artifact_id": "artifact-a", "sha256": "should-not-use"},
        )
    item = {
        "item_id": "source-book-17", "artifact_id": "artifact-a",
        "local_path": str(local), "media_format": "EPUB",
        "title": "Titre", "provider_id": "kobo",
        "metadata": {"publisher": "Maison"},
    }
    ctx = _build(item, route="search", storage=NS(artifacts=artifacts))
    assert calls == ["artifacts"]
    assert ctx.route == "search"
    assert isinstance(ctx.artifact, ArtifactRef)
    assert ctx.artifact.artifact_id == "artifact-a"
    assert ctx.artifact.book_id == "source-book-17"
    assert ctx.artifact.sha256 == "correct-sha"
    assert ctx.artifact.filename == "original.epub"
    assert ctx.artifact.size_bytes == 9
    assert ctx.artifact.format.media_type == "application/epub+zip"
    assert ctx.artifact.format.format == "EPUB"


def test_local_path_falls_back_to_metadata_without_new_scan(tmp_path):
    local = tmp_path / "example.pdf"
    local.write_bytes(b"%PDF")
    ctx = _build({
        "key": "logical-key",
        "artifact_id": "artifact-pdf",
        "metadata": {"path": str(local)},
    })
    assert ctx.book.book_id == "logical-key"
    assert ctx.artifact.book_id == "logical-key"
    assert ctx.artifact.size_bytes == 4
    assert ctx.artifact.filename == "example.pdf"
    assert ctx.artifact.format.format == "pdf"
    assert ctx.artifact.format.media_type == "application/octet-stream"


def test_missing_local_file_is_not_a_fabricated_size():
    ctx = _build({
        "item_id": "book-a", "artifact_id": "artifact-a",
        "local_path": "/definitely-not-here-cc91c/book.epub",
    }, storage=NS(artifacts=lambda: ({"artifact_id": "artifact-a", "sha256": "known"},)))
    assert ctx.artifact.size_bytes == 0
    assert ctx.artifact.sha256 == "known"
    assert ctx.artifact.format.media_type == "application/epub+zip"


def test_storage_failure_does_not_leak_exception_or_invent_hash(tmp_path):
    local = tmp_path / "ok.epub"
    local.write_bytes(b"hello")
    def broken():
        raise RuntimeError("credentials=secret")
    ctx = _build({
        "artifact_id": "a", "key": "book-b", "local_path": str(local),
    }, storage=NS(artifacts=broken), route=None)
    assert ctx.route == ""
    assert ctx.artifact.sha256 == ""
    assert ctx.artifact.size_bytes == 5
    assert ctx.artifact.filename == "ok.epub"


def test_no_artifact_id_never_creates_fake_artifact(tmp_path):
    local = tmp_path / "valid.epub"
    local.write_bytes(b"hello")
    ctx = _build({
        "local_path": str(local), "key": "book-c", "media_format": "epub",
    }, storage=NS(artifacts=lambda: (_ for _ in ()).throw(
        AssertionError("unexpected storage lookup")
    )))
    assert ctx.book.book_id == "book-c"
    assert ctx.artifact is None


def test_no_local_path_even_with_id_does_not_invent_file():
    ctx = _build({
        "artifact_id": "artifact-one", "key": "book-d", "title": "No local file",
    }, storage=NS(artifacts=lambda: (_ for _ in ()).throw(
        AssertionError("unexpected storage lookup")
    )))
    assert ctx.artifact is None
    assert ctx.book.book_id == "book-d"


def test_metadata_and_public_book_are_independent_dict_projections():
    row = {"item_id": "book-e", "metadata": {"series": "A"}}
    ctx = _build(row)
    assert ctx.book.metadata == {"series": "A"}
    assert ctx.book.metadata is not row["metadata"]
    assert ctx.metadata is not ctx.book.metadata
    ctx.metadata["series"] = "B"
    assert row["metadata"]["series"] == "A"
    assert ctx.book.metadata["series"] == "A"
