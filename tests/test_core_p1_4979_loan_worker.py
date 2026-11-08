"""P1 #4979: source-scoped local cover snapshot is loaded off the GTK thread."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.models import Loan

PATH = Path(__file__).resolve().parents[1] / "src/cifereca/gui/loans_controller.py"


def refresh_method():
    source = ast.parse(PATH.read_text(encoding="utf-8"))
    function = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                    and n.name == "refresh_loans")
    namespace = {
        "Loan": Loan,
        "Gtk": NS(Widget=type("Widget", (), {})),
        "TaskPriority": NS(USER_ACTION="user", ACTIVE_LOAN="loan"),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 str(PATH), "exec"), namespace)
    return namespace["refresh_loans"]


def fixture(*, with_image=False, fail_local=False):
    rows = [Loan(
        id="loan-1", provider="paris", medium="digital",
        record_id="edition-1", title="Titre",
        source_provider="bnf-pret", source_credential="card-a",
        source_account_id="borrower-a", identity_id="alice",
        thumbnail_url="https://example.test/cover.png" if with_image else None,
    )]
    state = []
    service = NS(
        current_identity=NS(identity_id="alice"), _account_context_epoch=42,
        refresh_identity_loans=lambda **kwargs: (state.append(("remote", kwargs)), rows)[1],
        library_entries=lambda **kwargs: (
            (_ for _ in ()).throw(OSError("local unavailable"))
            if fail_local else (state.append(("local", kwargs)), [{"identity_id": "alice"}])[1]
        ),
    )
    class Runner:
        def submit(self, message, work, done, **options):
            state.append(("submit", message))
            done(work())
    host = NS(
        service=service, runner=Runner(),
        _loans_refreshed=lambda value: state.append(("present", value)),
    )
    return host, rows, state


def test_missing_cover_triggers_single_local_snapshot_after_loan_refresh():
    host, rows, events = fixture()
    refresh_method()(host, force=False)
    assert [e[0] for e in events] == ["submit", "remote", "local", "present"]
    assert events[1][1] == {"force": False, "identity_id": "alice"}
    assert events[2][1] == {"identity_id": "alice", "scan": False}
    assert events[-1][1] is rows
    assert host._loan_cover_projection == (42, "alice", ({"identity_id": "alice"},))


def test_provider_thumbnail_skips_unnecessary_library_disk_projection():
    host, rows, events = fixture(with_image=True)
    refresh_method()(host)
    assert [e[0] for e in events] == ["submit", "remote", "present"]
    assert host._loan_cover_projection == (42, "alice", ())


def test_failed_local_cover_scan_never_discards_valid_loan_inventory():
    host, rows, events = fixture(fail_local=True)
    refresh_method()(host)
    assert [e[0] for e in events] == ["submit", "remote", "present"]
    assert events[-1][1] is rows
    assert host._loan_cover_projection == (42, "alice", ())


def test_account_epoch_change_discarded_before_any_cover_or_loan_ui_update():
    host, rows, events = fixture()
    def changed_epoch(**kwargs):
        events.append(("remote", kwargs))
        host.service._account_context_epoch += 1
        return rows
    host.service.refresh_identity_loans = changed_epoch
    refresh_method()(host)
    assert not hasattr(host, "_loan_cover_projection")
    assert not any(kind == "present" for kind, *_ in events)


def test_foreign_borrower_rows_never_trigger_cover_lookup():
    host, rows, events = fixture()
    rows[0].identity_id = "bob"
    refresh_method()(host)
    assert [e[0] for e in events] == ["submit", "remote", "present"]
    assert host._loan_cover_projection == (42, "alice", ())
