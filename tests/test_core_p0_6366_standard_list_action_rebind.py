"""#6366/#3104: standard list rows cannot retain retired action capabilities.

A preserved Gtk.ListItem binds a callback at paint time. Equal labels/titles,
URLs and even exact DocumentRefs are not permission to reuse an OLD closure
after an account/source/offer epoch changes.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/master_detail.py"


def same_row():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.FunctionDef)
        and stmt.name == "_same_book_row_presentation"
    )
    visible_fields = next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_BOOK_ROW_VISIBLE_FIELDS"
            for target in stmt.targets
        )
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")],
                level=0,
            ),
            visible_fields,
            function,
        ],
        type_ignores=[],
    ))
    env = {}
    exec(compile(module, str(SOURCE), "exec"), env)
    return env["_same_book_row_presentation"]


def row(callback=None, *, document=None, title="One book"):
    return NS(
        title=title, subtitle="Author", meta="Book", badge="",
        badge_style="", thumbnail_url="https://covers.example.org/same.jpg",
        default_thumbnail_url=None, cover_provider=None,
        cover_record_id=None, cover_title="One book",
        cover_author=None, cover_year=None,
        action_label="Télécharger" if callback is not None else "",
        action_icon_name="document-save-symbolic",
        action_callback=callback,
        _cover_document_ref=document,
        _public_row_content=("same visible row",),
    )


def test_no_action_and_same_exact_action_keep_the_fast_path():
    same = same_row()
    assert same(row(), row())
    action = lambda _button: None
    assert same(row(action), row(action))


def test_changed_same_label_action_is_not_reused_across_account_or_offer():
    same = same_row()
    events = []
    old_action = lambda _button: events.append("old-account")
    new_action = lambda _button: events.append("new-account")
    before, after = row(old_action), row(new_action)
    assert before.title == after.title
    assert before._cover_document_ref == after._cover_document_ref
    assert before.thumbnail_url == after.thumbnail_url
    assert before.action_label == after.action_label
    assert not same(before, after)

    # Old action must never be treated as a valid handler for the new row;
    # MasterDetailPage.show_rows will splice and rebind the new Gtk.ListItem.
    after.action_callback(None)
    assert events == ["new-account"]


def test_action_revoked_or_newly_admitted_must_rebind_even_with_same_cover():
    same = same_row()
    allowed = row(lambda _button: None)
    revoked = row()
    # Equal visual text/action labels are possible when a producer retains
    # presentation labels while revoking an executable capability.
    revoked.action_label = allowed.action_label
    assert not same(allowed, revoked)
    assert not same(revoked, allowed)


def test_callback_equality_override_is_not_a_permission_lease():
    same = same_row()

    class AlwaysEqualAction:
        def __eq__(self, other):
            return True

        def __call__(self, button):
            pass

    a = AlwaysEqualAction()
    b = AlwaysEqualAction()
    assert a == b and a is not b
    assert not same(row(a), row(b))
