"""#6168: only a visible cell registers an already attested Core cover."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _method(name):
    tree = ast.parse((ROOT / "book_widgets.py").read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef)
               and x.name == "BookListCell")
    fn = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
              and x.name == name)
    scope = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), fn],
             type_ignores=[])), str(ROOT / "book_widgets.py"), "exec"), scope)
    return scope[name]


def test_visible_cell_admits_only_exact_core_packet():
    call = []
    facts = object()
    document = object()
    service = object()
    class Adapter:
        def register(self, received_service, received_facts):
            call.append((received_service, received_facts))
            return document
    window = SimpleNamespace(local_cover_adapter=Adapter())
    cell = SimpleNamespace(window=window)
    item = SimpleNamespace(_cover_local_evidence=facts)
    admit = _method("_admit_core_local_cover")
    assert admit(cell, item, service, document)
    assert call == [(service, facts)]
    assert not admit(cell, item, service, object())


def test_no_unverified_packet_is_published():
    class Adapter:
        def register(self, *_):
            raise AssertionError("must not register")
    cell = SimpleNamespace(window=SimpleNamespace(local_cover_adapter=Adapter()))
    item = SimpleNamespace(_cover_local_evidence=None)
    assert _method("_admit_core_local_cover")(cell, item, object(), object())


def test_malformed_or_closed_adapter_fails_closed():
    class Adapter:
        def register(self, *_):
            raise RuntimeError("closed")
    cell = SimpleNamespace(window=SimpleNamespace(local_cover_adapter=Adapter()))
    item = SimpleNamespace(_cover_local_evidence=object())
    assert not _method("_admit_core_local_cover")(cell, item, object(), object())


def test_binding_and_signal_both_publish_before_n2_materialization():
    source = (ROOT / "book_widgets.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef)
               and x.name == "BookListCell")
    for name in ("bind", "_thumbnail_changed"):
        fn = next(x for x in cls.body if isinstance(x, ast.FunctionDef)
                  and x.name == name)
        body = ast.unparse(fn)
        assert "self._admit_core_local_cover(item, service, document)" in body
        assert body.index("self._admit_core_local_cover") < body.index(
            "self.cover.bind_document("
        )
    assert "self.cover.bind_url(self.cover_loader, None, priority=10)" in source


def test_shared_registry_is_window_owned_and_closed_before_n2():
    start = (ROOT / "main_window.py").read_text(encoding="utf-8")
    end = (ROOT / "window_close.py").read_text(encoding="utf-8")
    assert "self.local_cover_adapter = LocalCoverEvidenceAdapter()" in start
    assert "adapter.close()" in end
    assert end.index("adapter.close()") < end.index("service.close()")
