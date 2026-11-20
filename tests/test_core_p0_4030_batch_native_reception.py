"""#4030: native Maintenance lifecycle and caller-owned SQLite transactions."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS

import pytest

from cifereca.core import local_origin_suggestion_preview as preview
from cifereca.gui.local_origin_suggestion_batch_line import add_local_origin_suggestion_batch_line
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows, BYTES
from tests.test_core_p0_4030_suggestion_operator import _service
from tests.test_core_p0_4030_suggestion_batch import _extra
from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container


class Label(Button):
    def __init__(self, **kwargs):
        super().__init__()
        self.text = ""

    def set_wrap(self, value): pass
    def set_selectable(self, value): pass
    def set_text(self, value): self.text = value


@pytest.mark.parametrize("scenario", ["page", "cancel_before", "cancel_after",
                                     "route_changed", "profile_aba", "detached"])
def test_native_batch_lifecycle(store, tmp_path, scenario):
    service, root, pid = _service(store, tmp_path)
    files = [Path(store.placement_get(pid)["path"])]
    files += [_extra(store, root, f"book-{i}.pdf") for i in range(4)]
    before = rows(store)
    tasks = []
    host = NS(service=service, _closed=False, _route_display_token=object(),
              action_button=lambda label, _: Button(label),
              runner=NS(submit=lambda *args, **kwargs: tasks.append(args)))
    gtk = NS(Box=lambda **kw: Container(), Orientation=NS(HORIZONTAL=0),
             Button=Button, Label=Label)
    adw = NS(PreferencesGroup=Container, ActionRow=Container)
    box = Container()
    assert add_local_origin_suggestion_batch_line(host, box, Gtk=gtk, Adw=adw)
    group, actions, detail = box.children
    start, = group.children[0].children
    following, cancel = actions.children
    assert not tasks and not detail.visible
    assert not following.sensitive and not cancel.sensitive
    start.click()
    start.click()
    following.click()
    assert len(tasks) == 1 and cancel.sensitive
    assert not following.sensitive and not start.sensitive
    _, work, done, _ = tasks[0]
    if scenario == "cancel_before":
        cancel.click()
    result = work()
    if scenario == "cancel_before":
        assert result is None
    else:
        assert result["page"] == 1 and len(result["entries"]) == 4
        assert result["next_cursor"] is not None
    if scenario == "cancel_after":
        cancel.click()
    elif scenario == "route_changed":
        host._route_display_token = object()
    elif scenario == "profile_aba":
        service._account_context_epoch += 2
    elif scenario == "detached":
        start.root = None
    done(result)
    done(result)  # late repeated delivery cannot create another page or result
    if scenario == "page":
        assert detail.visible and "Page 1" in detail.text
        assert following.sensitive and not cancel.sensitive and start.sensitive
        following.click()
        assert len(tasks) == 2
        _, work, done, _ = tasks[1]
        second = work()
        assert second["page"] == 2 and len(second["entries"]) == 1
        done(second)
        assert "Page 2" in detail.text and not following.sensitive
    else:
        assert not following.sensitive
        assert "Page 1" not in detail.text and "Roman commun" not in detail.text
        if scenario in {"cancel_before", "cancel_after"}:
            assert "Revue arrêtée" in detail.text
            assert start.sensitive and not cancel.sensitive
            start.click()
            assert len(tasks) == 2
        elif scenario != "detached":
            assert not detail.visible and detail.text == ""
    assert rows(store) == before
    assert all(path.read_bytes() == BYTES for path in files)


@pytest.mark.parametrize("operation", ["candidate", "documentary"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_individual_review_cannot_rollback_caller_write(store, tmp_path, operation, finish):
    service, root, pid = _service(store, tmp_path)
    source = Path(store.placement_get(pid)["path"])
    baseline = rows(store)
    db = store._connect()
    sentinel = "synthetic.4030.caller"
    other = sqlite3.connect(store.path)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (sentinel, "pending"))
        options = dict(library_root=root, identity_id="A", placement_id=pid)
        if operation == "candidate":
            result = preview.preview_local_origin_suggestion(store, **options)
        else:
            result = preview.suggest_local_placement_documents(
                store, explicitly_requested=True, **options)
        assert result["status"] == "excluded" and not result["may_write_origin_or_right"]
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key=?", (sentinel,)).fetchone() == ("pending",)
        assert other.execute("SELECT value FROM metadata WHERE key=?", (sentinel,)).fetchone() is None
        if finish == "commit":
            db.commit()
        else:
            db.rollback()
        assert other.execute("SELECT value FROM metadata WHERE key=?", (sentinel,)).fetchone() == (
            ("pending",) if finish == "commit" else None)
        result = preview.suggest_local_placement_documents(store, explicitly_requested=True, **options)
        assert result["status"] == "partial" and result["suggestions"].known_matches == 1
        assert rows(store) == baseline and source.read_bytes() == BYTES
    finally:
        if db.in_transaction:
            db.rollback()
        other.close()
