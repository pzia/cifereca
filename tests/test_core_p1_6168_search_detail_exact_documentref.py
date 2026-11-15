"""#6168 Search local detail: same Core DocumentRef as its visible list cell."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import project_verified_local_cover_evidence
from cifereca.gui.detail_model import book_detail_presentation
from tests.test_epub_cover_contract import _write_epub


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui/search_details.py"


class Button:
    def __init__(self, label):
        self.label = label
    def connect(self, *_a):
        pass
    def add_css_class(self, *_a):
        pass


def actual_method():
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    method = next(
        x for x in tree.body
        if isinstance(x, ast.FunctionDef) and x.name == "_search_selected"
    )
    scope = dict(
        book_detail_presentation=book_detail_presentation,
        local_book_detail_sections=lambda *_a, **_kw: [],
        detail_language=lambda *_: "",
        Gtk=NS(Button=Button),
    )
    exec(compile(ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0,
        ), method,
    ], type_ignores=[])), str(ROOT), "exec"), scope)
    return scope["_search_selected"]


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "book.epub"
    data = _write_epub(book, epub3=True)
    sha = hashlib.sha256(data).hexdigest()
    facts = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + sha,
        artifact_id="sha256:" + sha, placement_id="placement:alice",
        artifact_sha256=sha, verified_path=book,
        artifact_metadata=epub_metadata.inspect_epub(book),
    )
    assert facts is not None and facts.embedded is not None
    row = dict(
        key=facts.scope.source_key, identity_id="alice",
        artifact_id=facts.scope.artifact_id,
        placement_id=facts.scope.placement_id, local=True,
        provider_id="local", source="local",
        title="Book", author="Author", metadata={},
        thumbnail_url="https://public.example.org/hot.png",
        _exact_local_cover_evidence=facts,
    )
    return row, facts


def host(row, *, current="alice"):
    events = []
    panel = NS(
        show_presentation=lambda pres, **kw: events.append(("url", pres, kw)),
        show_local_presentation=lambda pres, **kw: events.append(("n2", pres, kw)),
        add_actions=lambda *_: None,
    )
    subject = NS(
        _closed=False, _search_detail_request_generation=0,
        _cancel_cached_book_details=lambda: None,
        _search_local_objects={row["key"]: row},
        search_page=NS(detail=panel),
        service=NS(
            current_identity=NS(identity_id=current),
            provider_display_name=lambda *_a: "Local",
            identity_by_id=lambda *_a: None,
        ),
        _book_description=lambda *_a: "",
        _library_availability_label=lambda *_a: "Local",
        _kobo_backup_status_label=lambda *_a: "",
        _open_library_item=lambda *_a: None,
        run_search=lambda *_a: None,
        _add_local_book_actions=lambda *_a: None,
        _add_facets=lambda *_a: None,
        _add_plugin_book_actions=lambda *_a: None,
        _request_cached_book_details=lambda *_a: None,
        search_controls=object(),
    )
    return subject, events


def test_search_local_epub_detail_selects_n2_before_legacy_image_url(local):
    row, facts = local
    image = epub_metadata.cover_cache_dir() / facts.embedded.resource.cache_name
    image.unlink()
    controller, events = host(row)
    actual_method()(controller, NS(key=row["key"]))
    assert len(events) == 1 and events[0][0] == "n2"
    presentation, kw = events[0][1:]
    assert presentation.thumbnail_url == row["thumbnail_url"]
    assert kw["cover_evidence"] is facts
    assert controller._selected_search_id == row["key"]
    assert not image.exists()


@pytest.mark.parametrize("alter", [
    {"identity_id": "bob"},
    {"artifact_id": "sha256:" + "b" * 64},
    {"placement_id": "placement:b"},
    {"key": "other-known-notice"},
    {"_exact_local_cover_evidence": object()},
])
def test_stale_or_untrusted_search_proof_never_downgrades_to_url(local, alter):
    row, _facts = local
    changed = {**row, **alter}
    controller, events = host(changed)
    actual_method()(controller, NS(key=changed["key"]))
    assert events[0][0] == "url"
    assert events[0][1].thumbnail_url is None


def test_active_borrower_switch_refuses_old_core_proof(local):
    row, _facts = local
    controller, events = host(row, current="bob")
    actual_method()(controller, NS(key=row["key"]))
    assert events[0][0] == "url"
    assert events[0][1].thumbnail_url is None


def test_old_local_search_notice_preserves_public_renderer(local):
    row, _facts = local
    legacy = {k: v for k, v in row.items()
              if k != "_exact_local_cover_evidence"}
    controller, events = host(legacy)
    actual_method()(controller, NS(key=legacy["key"]))
    assert events[0][0] == "url"
    assert events[0][1].thumbnail_url == legacy["thumbnail_url"]


def test_detail_keeps_single_source_builder_without_direct_provider_io():
    source = ROOT.read_text(encoding="utf-8")
    local = source[source.index("def _search_selected("):
                   source.index("def _publish_search_detail_cover(")]
    assert "exact_local_detail_cover(" in local
    assert "detail.show_local_presentation(" in local
    assert "cover_evidence=cover_evidence" in local
    assert "detail.show_presentation(" in local
    assert "book_detail_presentation(" in local
    assert "inspect_epub(" not in local
    assert "get_cover(" not in local  # Core-owned N2 service is the only resolver.
