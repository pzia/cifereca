"""P1 #4979: a later scoped Library snapshot repaints the loan list.

All assertions exercise the real controller functions parsed from production
source. No Gtk runtime, provider account, filesystem scan or HTTP is required.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.core.loan_cover_fallback import materialized_loan_cover_rows
from tests.test_core_p1_4979_loan_gtk_projection import a_loan, a_row, ui_method
from tests.test_core_p1_4979_loan_worker import refresh_method


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/cifereca/gui/loans_controller.py"
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"


def snapshot_method():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    func = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "_library_loan_covers_updated")
    space = {"materialized_loan_cover_rows": materialized_loan_cover_rows,
             "Any": object}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])),
                 str(SOURCE), "exec"), space)
    return space[func.name]


def host(*, identity="alice", epoch=42):
    loan = a_loan(identity="alice")
    views = []
    service = NS(current_identity=NS(identity_id=identity),
                 _account_context_epoch=epoch, last_loans=[loan])
    obj = NS(service=service, loan_page=NS(), _library_entries_cache=[],
             _library_all_profiles=False, _loan_cover_projection=(epoch, identity, ()))
    def repaint():
        views.append(ui_method()(obj, loan).thumbnail_url)
    obj._render_loans = repaint
    return obj, loan, views


def test_late_exact_local_proof_repaints_same_loan_without_mutating_supplier():
    obj, loan, views = host()
    repaint = obj._render_loans
    repaint()
    assert views == [None]
    cover = a_row(loan)
    snapshot_method()(obj, [cover])
    assert views == [None, cover["thumbnail_url"]]
    assert obj._loan_cover_projection == (42, "alice", (cover,))
    assert obj._loan_cover_library_revision == 1
    assert loan.thumbnail_url is None
    snapshot_method()(obj, [cover])
    assert views[-1] == cover["thumbnail_url"]  # cold/warm idempotence


def test_foreign_or_global_library_snapshot_cannot_repaint_loan():
    obj, loan, views = host()
    foreign = a_row(a_loan(identity="bob"))
    snapshot_method()(obj, [foreign])
    assert views == [None]
    assert obj._loan_cover_projection == (42, "alice", (foreign,))
    assert obj._loan_cover_library_revision == 1
    obj._library_all_profiles = True
    snapshot_method()(obj, [a_row(loan)])
    assert views == [None]
    assert obj._loan_cover_library_revision == 1
    obj._library_all_profiles = False
    obj.service.current_identity.identity_id = "bob"
    snapshot_method()(obj, [a_row(loan)])
    assert views[-1] is None  # cannot project Alice under Bob
    obj._closed = True
    revision = obj._loan_cover_library_revision
    snapshot_method()(obj, [a_row(loan)])
    assert obj._loan_cover_library_revision == revision


def test_empty_completed_library_revokes_stale_cover_and_bad_snapshot_is_rejected():
    obj, loan, views = host()
    snapshot_method()(obj, [a_row(loan)])
    assert views[-1] is not None
    snapshot_method()(obj, [])
    assert views[-1] is None
    assert obj._loan_cover_projection == (42, "alice", ())
    revision = obj._loan_cover_library_revision
    snapshot_method()(obj, [{"identity_id": "alice"}] * 10001)
    assert obj._loan_cover_library_revision == revision
    assert views[-1] is None


def test_inflight_loan_worker_must_not_overwrite_fresher_library_projection():
    obj, loan, views = host()
    pending = []
    obj._loans_refreshed = lambda loans: views.append(
        ui_method()(obj, loans[0]).thumbnail_url
    )
    obj.runner = NS(submit=lambda _label, work, done, **_kwargs:
                    pending.append((work, done)))
    obj.service.refresh_identity_loans = lambda **_kwargs: [loan]
    obj.service.library_entries = lambda **_kwargs: []  # old loan worker
    refresh_method()(obj)
    assert len(pending) == 1
    cover = a_row(loan)
    snapshot_method()(obj, [cover])
    work, done = pending.pop()
    done(work())  # network/old Library worker completes after the new snapshot
    assert obj._loan_cover_projection == (42, "alice", (cover,))
    assert views[-1] == cover["thumbnail_url"]
    assert loan.thumbnail_url is None


def test_main_window_delivery_calls_loan_repaint_after_library_render():
    src = MAIN_WINDOW.read_text(encoding="utf-8")
    tree = ast.parse(src)
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_show_library_entries")
    rendered = ast.unparse(method)
    assert "self._library_entries_cache = list(entries)" in rendered
    assert "self._render_library(entries)" in rendered
    assert "loans_controller._library_loan_covers_updated(self, entries)" in rendered
    assert rendered.index("self._render_library(entries)") < rendered.index(
        "loans_controller._library_loan_covers_updated(self, entries)"
    )
