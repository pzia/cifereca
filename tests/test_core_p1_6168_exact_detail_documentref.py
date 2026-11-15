"""#6168: Library + Reader list/detail consume one Core-proved DocumentRef.

The detail sink is executed from the REAL production method via AST without
requiring a GTK display. N2/N3 are the genuine shared service and loader.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace as NS

import pytest

from cifereca import epub_metadata
from cifereca.core.local_cover_evidence import (
    VerifiedLocalCoverEvidence, project_verified_local_cover_evidence,
)
from cifereca.core.covers import candidate
from cifereca.gui.cover_loader import CoverLoader
from cifereca.gui.document_cover_service import DocumentCoverService
from cifereca.gui.local_cover_adapter import LocalCoverEvidenceAdapter
from cifereca.gui.local_detail_cover import exact_local_detail_cover
from tests.test_epub_cover_contract import _write_epub


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
ORIGIN = "https://covers.public.example.org/source.png"


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    book = tmp_path / "exact.epub"
    raw = _write_epub(book, epub3=True)
    digest = hashlib.sha256(raw).hexdigest()
    data = epub_metadata.inspect_epub(book)
    facts = project_verified_local_cover_evidence(
        identity_id="alice", book_key="artifact:sha256:" + digest,
        artifact_id="sha256:" + digest, placement_id="place:a",
        artifact_sha256=digest, artifact_metadata=data, verified_path=book,
        candidates=(candidate(
            ORIGIN, tier="origin", source="original-publisher",
            evidence_id="edition:exact",
        ),),
    )
    assert type(facts) is VerifiedLocalCoverEvidence
    assert facts.embedded is not None
    row = {
        "key": facts.scope.source_key,
        "identity_id": facts.scope.identity_id,
        "artifact_id": facts.scope.artifact_id,
        "placement_id": facts.scope.placement_id,
        "thumbnail_url": ORIGIN,
        "_exact_local_cover_evidence": facts,
    }
    loader = CoverLoader(max_workers=2)
    service = DocumentCoverService(loader)
    adapter = LocalCoverEvidenceAdapter()
    host = NS(covers=loader, document_covers=service, local_cover_adapter=adapter)
    try:
        yield facts, row, book, host
    finally:
        adapter.close()
        service.close()
        loader.close()


def _production_bind_cover():
    tree = ast.parse((ROOT / "detail_panel.py").read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef)
               and x.name == "DetailPanel")
    fn = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
              and x.name == "_bind_cover")
    scope = {
        "__name__": "cifereca.gui.detail_panel",
        "__package__": "cifereca.gui",
    }
    ast_module = ast.Module(body=[
        ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")], level=0,
        ),
        fn,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(ast_module),
                 str(ROOT / "detail_panel.py"), "exec"), scope)
    return scope["_bind_cover"]


def test_exact_local_detail_ref_only_matches_this_owner_and_placement(prepared):
    facts, item, _book, _host = prepared
    assert exact_local_detail_cover(item, active_identity_id="alice") is facts
    assert exact_local_detail_cover(
        item, active_identity_id="alice",
        artifact_id=facts.scope.artifact_id,
        placement_id=facts.scope.placement_id,
    ) is facts
    assert exact_local_detail_cover(
        item, active_identity_id="bob",
    ) is None
    assert exact_local_detail_cover(
        {**item, "_library_view_owner": "bob"},
        active_identity_id="alice",
    ) is None
    for key, replacement in (
        ("key", "unrelated"),
        ("identity_id", "bob"),
        ("artifact_id", "sha256:" + "a" * 64),
        ("placement_id", "another-physical-file"),
    ):
        assert exact_local_detail_cover(
            {**item, key: replacement}, active_identity_id="alice",
        ) is None, key
    assert exact_local_detail_cover(
        item, active_identity_id="alice", placement_id="other",
    ) is None


def test_missing_private_packet_does_not_infer_from_matching_title_or_uri(prepared):
    _facts, item, _book, _host = prepared
    for raw in (
        {key: val for key, val in item.items()
         if key != "_exact_local_cover_evidence"},
        {**item, "_exact_local_cover_evidence": object()},
        {**item, "_exact_local_cover_evidence": "known-sha"},
    ):
        assert exact_local_detail_cover(
            raw, active_identity_id="alice",
        ) is None
    assert exact_local_detail_cover(item, active_identity_id=None) is None
    assert exact_local_detail_cover(object(), active_identity_id="alice") is None


def test_list_and_detail_share_single_n2_decision_after_n3_eviction(prepared):
    facts, row, _book, host = prepared
    physical = epub_metadata.cover_cache_dir() / facts.embedded.resource.cache_name
    assert physical.is_file()
    physical.unlink()
    # List and detail N1 decisions are both resolved by DocumentCoverService.
    doc = host.local_cover_adapter.register(host.document_covers, facts)
    events = []
    host.document_covers.decisions.subscribe(doc, events.append)
    list_ready = Event()
    list_paths = []
    host.document_covers.get_cover(
        doc, "list",
        lambda path: (list_paths.append(path), list_ready.set()),
        width=180, height=270,
    )
    assert list_ready.wait(10)
    assert list_paths[0] is not None
    assert physical.is_file()

    detail_ready = Event()
    observed = []
    class Cover:
        def bind_document(self, service, document, *, variant, priority):
            observed.append((service, document, variant, priority))
            service.get_cover(
                document, variant,
                lambda path: (observed.append(path), detail_ready.set()),
            )
        def bind_url(self, *_args, **_kwargs):
            pytest.fail("proved EPUB must not downgrade to origin URL")

    _production_bind_cover()(
        NS(window=host), Cover(), row["thumbnail_url"], cover_evidence=(
            exact_local_detail_cover(row, active_identity_id="alice")
        ),
    )
    assert detail_ready.wait(10)
    assert observed[0] == (host.document_covers, doc, "detail", 0)
    assert observed[1] == physical
    assert [event.kind for event in events] == ["refresh", "resolved"]
    decision = host.document_covers.decisions.peek(doc, revision=1)
    assert (decision.status, decision.tier) == ("selected", "embedded")
    assert host.covers.stats()["download_ok"] == 0


def test_detail_proof_missing_uses_legacy_url_only_for_unattested_source(prepared):
    facts, row, _book, host = prepared
    bindings = []
    cover = NS(
        bind_document=lambda *_args, **_kwargs: bindings.append("n2"),
        bind_url=lambda _loader, url, **_kwargs: bindings.append(url),
    )
    _production_bind_cover()(NS(window=host), cover, ORIGIN, cover_evidence=None)
    assert bindings == [ORIGIN]
    # An explicit but untrusted proof never silently falls back to origin.
    _production_bind_cover()(
        NS(window=host), cover, ORIGIN, cover_evidence=object(),
    )
    assert bindings == [ORIGIN, None]


def test_detail_closed_registry_or_malformed_evidence_fails_closed(prepared):
    facts, _row, _book, host = prepared
    called = []
    cover = NS(
        bind_document=lambda *_args, **_kwargs: called.append("document"),
        bind_url=lambda _loader, uri, **_kwargs: called.append(uri),
    )
    host.local_cover_adapter.close()
    _production_bind_cover()(
        NS(window=host), cover, ORIGIN, cover_evidence=facts,
    )
    assert called == [None]


def test_global_or_foreign_profile_cannot_reuse_alice_selected_n2(prepared):
    facts, row, _book, host = prepared
    alice = host.local_cover_adapter.register(host.document_covers, facts)
    other = replace(facts, scope=replace(
        facts.scope, identity_id="bob", placement_id="place:b",
    ))
    # A forged metadata packet cannot be received from the foreign global
    # projection; the exact detail gate checks the row's owner and Placement.
    forged = {**row, "_exact_local_cover_evidence": other}
    assert exact_local_detail_cover(
        forged, active_identity_id="alice",
    ) is None
    assert host.document_covers.decisions.peek(other.scope, revision=1) is None
    assert alice != other.scope


def test_library_reader_and_shared_detail_have_explicit_evidence_handoff():
    lib = (ROOT / "library_detail_controller.py").read_text(encoding="utf-8")
    reader = (ROOT / "reader_page_controller.py").read_text(encoding="utf-8")
    panel = (ROOT / "detail_panel.py").read_text(encoding="utf-8")
    assert "exact_local_detail_cover(item, active_identity_id=active)" in lib
    assert "cover_evidence=cover_evidence" in lib
    assert 'getattr(window, "_reader_source_items", {}).get(row.key)' in reader
    assert "artifact_id=item.artifact_id" in reader
    assert "placement_id=item.placement_id" in reader
    assert "cover_evidence = exact_local_detail_cover(" in reader
    assert "detail.show_local_presentation(" in lib
    assert "page.detail.show_local_presentation(" in reader
    assert "self._bind_cover(" in panel
    assert "cover_evidence=cover_evidence" in panel
    assert 'variant="detail"' in panel


def test_detail_public_facade_signatures_remain_stable_for_all_consumers():
    panel = (ROOT / "detail_panel.py").read_text(encoding="utf-8")
    tree = ast.parse(panel)
    owner = next(x for x in tree.body
                 if isinstance(x, ast.ClassDef) and x.name == "DetailPanel")
    methods = {x.name: x for x in owner.body
               if isinstance(x, ast.FunctionDef)}
    for name in ("_cover_and_title", "show_presentation", "show_summary"):
        method = methods[name]
        args = [arg.arg for arg in method.args.kwonlyargs]
        assert "cover_evidence" not in args, name
    assert "show_local_presentation" in methods
    body = ast.unparse(methods["show_local_presentation"])
    assert "self._next_cover_evidence = cover_evidence" in body
    assert "finally:" in body
    assert "self._next_cover_evidence = None" in body
    assert "essential=essential" in ast.unparse(methods["show_presentation"])
    # Existing plugin and search detail widgets have exactly their legacy
    # facade signature. New local proof is never a required public argument.

