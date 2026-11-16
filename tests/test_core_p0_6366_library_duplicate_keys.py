"""P0 #6366: duplicate Library GTK keys must not bind the wrong C/detail."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_c_local_origin_projection import VerifiedLocalPublicCLink
from cifereca.gui.library_detail_controller import library_selected
from cifereca.gui.library_rows import project_library_rows


class Row:
    def __init__(self, *args):
        self.key = args[0]
        self.args = args


def library(*, key="artifact:shared", title="Homonyme",
            placement="placement:one", url="https://covers.example.org/a.jpg"):
    return {
        "key": key, "identity_id": "A",
        "artifact_id": "sha256:" + "a" * 64,
        "placement_id": placement, "local": True,
        "provider_id": "local", "providers": ["local"],
        "state": "local", "source": "catalog", "title": title,
        "author": "Une autrice", "thumbnail_url": url,
        "metadata": {},
    }


def public_link(item, edition):
    return VerifiedLocalPublicCLink(
        identity_id=item["identity_id"],
        artifact_id=item["artifact_id"],
        placement_id=item["placement_id"],
        document=canonical_cover_document_ref(edition),
        epoch=1, source_refs=("notice-c:v1:" + "a" * 64,),
    )


def project(*items):
    return project_library_rows(
        items, row_factory=Row,
        provider_display_name=lambda value: value,
        availability_label=lambda _: "Local",
        backup_status_label=lambda _: "",
        format_date=str,
    )


def test_unique_key_keeps_attested_c_list_ref():
    a = library()
    proof = public_link(a, "edition:first")
    a["_public_canonical_cover_link"] = proof
    rows, objects = project(a)
    assert objects[a["key"]] is a
    assert len(rows) == 1
    assert rows[0]._cover_document_ref == proof.document
    assert rows[0].args[5] is None
    assert not getattr(rows[0], "_library_ambiguous_key", False)


def test_duplicate_key_suppresses_both_c_refs_legacy_urls_and_selection():
    a = library(title="Premier", placement="placement:a")
    b = library(
        title="Second", placement="placement:b",
        url="https://covers.example.org/b.jpg",
    )
    a["_public_canonical_cover_link"] = public_link(a, "edition:first")
    b["_public_canonical_cover_link"] = public_link(b, "edition:second")
    rows, objects = project(a, b)
    assert len(rows) == 2
    assert objects[a["key"]] is b  # Legacy mapping is last-write-wins.
    for row in rows:
        assert row.args[5] is None
        assert not hasattr(row, "_cover_document_ref")
        assert row._library_ambiguous_key is True
        assert "ambiguous-library-key" in row._public_row_content

    events = []
    host = NS(
        _closed=False, _cancel_cached_book_details=lambda: None,
        _library_objects=objects,
        library_page=NS(detail=NS(
            empty=lambda *args: events.append(args),
        )),
    )
    library_selected(
        host, rows[0],
        Gtk=None, unique_library_loan=None,
        book_detail_presentation=None, detail_language=None,
        document_links=None,
    )
    assert events and events[0][0] == "Référence ambiguë"
    # No source/profile/DRM action was possible before the early return.


def test_duplicate_key_suppresses_direct_legacy_url_without_c_packet():
    a = library(url="https://covers.example.org/a.jpg")
    b = library(url="https://covers.example.org/b.jpg")
    rows, _ = project(a, b)
    assert [row.args[5] for row in rows] == [None, None]
    assert all(row._library_ambiguous_key for row in rows)


def test_unique_key_revives_after_collision_is_removed():
    a = library()
    b = library(placement="placement:b")
    a["_public_canonical_cover_link"] = public_link(a, "edition:first")
    ambiguous, _ = project(a, b)
    unique, _ = project(a)
    assert "ambiguous-library-key" in ambiguous[0]._public_row_content
    assert not getattr(unique[0], "_library_ambiguous_key", False)
    assert unique[0]._cover_document_ref == a["_public_canonical_cover_link"].document
    assert ambiguous[0]._public_row_content != unique[0]._public_row_content


def test_missing_key_preserves_original_unique_index_fallback():
    a = library(key="known")
    b = library(key="known")
    c = library(key="")
    rows, _ = project(a, b, c)
    assert [row.key for row in rows] == ["known", "known", "library:1"]


def test_stale_once_unique_gtk_row_cannot_open_new_duplicate_mapping():
    old = library(title="Previously unique")
    stale_row, _ = project(old)
    assert not getattr(stale_row[0], "_library_ambiguous_key", False)
    current = library(title="Different book", placement="placement:other")
    events = []
    host = NS(
        _closed=False, _cancel_cached_book_details=lambda: None,
        _library_objects={old["key"]: current},
        _library_ambiguous_keys=frozenset((old["key"],)),
        library_page=NS(detail=NS(empty=lambda *args: events.append(args))),
    )
    library_selected(
        host, stale_row[0], Gtk=None, unique_library_loan=None,
        book_detail_presentation=None, detail_language=None,
        document_links=None,
    )
    assert events and events[0][0] == "Référence ambiguë"


def test_render_controller_publishes_ambiguity_set_before_gtk_model_update():
    from pathlib import Path
    import ast
    path = Path(__file__).resolve().parents[1] / (
        "src/cifereca/gui/library_render_controller.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    method = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_library"
    )
    code = ast.unparse(method)
    assert code.index("window._library_ambiguous_keys") < code.index(
        "window.library_page.show_rows("
    )
