"""#1497: a malformed metadata book or placement cannot poison a repair plan.

Synthetic broker only; never rename files, access GTK or issue network calls.
"""
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingRepairTool


class Bomb:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK")

    def __bool__(self):
        raise RuntimeError("PRIVATE_ACCOUNT")


class BadTitle(dict):
    def get(self, name, default=None):
        if name == "title":
            raise RuntimeError("PRIVATE_TITLE")
        return super().get(name, default)


class BadBook:
    def __getattribute__(self, name):
        if name == "authors":
            raise RuntimeError("PRIVATE_AUTHOR")
        return object.__getattribute__(self, name)


def placement(identifier, filename="wrong.epub"):
    return NS(kind="library", identity_id="alice", placement_id=identifier, filename=filename)


class LibraryRead:
    def __init__(self, views):
        self.views = views
        self.calls = []

    def for_artifact(self, identifier):
        self.calls.append(identifier)
        return self.views[identifier]


class Context:
    def __init__(self, rows, views):
        self.rows = rows
        self.read = LibraryRead(views)
        self.preview_calls = []

    def broker(self, name):
        if name == "metadata":
            return NS(library_entries=lambda **kw: tuple(self.rows))
        if name == "library.read":
            return self.read
        if name == "library.naming":
            def preview(**values):
                self.preview_calls.append(values["title"])
                return Bomb() if values["title"] == "PRIVATE_BAD_POLICY" else "correct.epub"
            return NS(preview=preview)
        raise AssertionError(name)


def test_broken_rows_are_skipped_but_valid_repair_candidates_survive():
    rows = [
        {"artifact_id": "good-a", "title": "A"},
        BadTitle(artifact_id="bad-title", title="PRIVATE_BAD_TITLE"),
        {"artifact_id": "bad-book", "title": "B"},
        {"artifact_id": "bad-placement", "title": "C"},
        {"artifact_id": "bad-proposal", "title": "PRIVATE_BAD_POLICY"},
        {"artifact_id": "good-b", "title": "D"},
    ]
    views = {
        "good-a": NS(book=NS(title="A", authors=()), placements=(placement("p1"),)),
        "bad-title": NS(book=NS(title="PRIVATE", authors=()), placements=(placement("secret"),)),
        "bad-book": NS(book=BadBook(), placements=(placement("secret"),)),
        "bad-placement": NS(book=NS(title="C", authors=()), placements=(placement("secret", Bomb()),)),
        "bad-proposal": NS(book=NS(title="PRIVATE_BAD_POLICY", authors=()), placements=(placement("private"),)),
        "good-b": NS(book=NS(title="D", authors=()), placements=(placement("p2"),)),
    }
    ctx = Context(rows, views)
    plan = NamingRepairTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert plan.facts == {
        "placement_ids": ("p1", "p2"),
        "skipped": 3, "policy_failures": 1, "read_failures": 0,
    }
    assert plan.changes == ("Renommer le placement n°1", "Renommer le placement n°2")
    assert len(ctx.read.calls) == 6
    assert ctx.preview_calls == ["A", "PRIVATE_BAD_POLICY", "D"]
    assert plan.warnings == (
        "3 entrée(s) non vérifiable(s) ignorée(s).",
        "1 placement(s) sans proposition valide de la politique Core.",
    )
    exported = json.dumps({
        "summary": plan.summary, "changes": plan.changes,
        "warnings": plan.warnings, "public_counts": {k: v for k, v in plan.facts.items() if k != "placement_ids"},
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "bad-title", "secret"):
        assert secret not in exported


@pytest.mark.parametrize("invalid", (True, None, Bomb(), "bad\nidentifier", "x" * 1025, " padded "))
def test_invalid_placement_id_is_not_ever_included_in_mutating_plan(invalid):
    row = {"artifact_id": "only", "title": "Safe title"}
    view = NS(book=NS(title="Safe title", authors=()), placements=(placement(invalid),))
    ctx = Context([row], {"only": view})
    plan = NamingRepairTool().activate(ctx).plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert plan.facts["placement_ids"] == ()
    assert plan.facts["skipped"] == 1
    assert plan.changes == ()
    assert ctx.preview_calls == []
