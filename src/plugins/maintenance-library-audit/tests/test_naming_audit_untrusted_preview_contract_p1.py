"""#1496: a Core naming preview is an attested filename, never an arbitrary value.

Headless brokers; no files opened, renamed, deleted, network or GTK activity.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingAuditTool


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_BROKER_SECRET")


class Policy:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def preview(self, **kw):
        self.calls.append(kw)
        return self.result


class Context:
    def __init__(self, proposal):
        self.rows = [
            {"title": "first", "filename": "safe.epub"},
            {"title": "second", "filename": "candidate.epub"},
        ]
        self.policy = Policy(proposal)

    def broker(self, name):
        if name == "metadata":
            return type("Metadata", (), {
                "library_entries": lambda _self, **_kw: self.rows,
            })()
        assert name == "library.naming"
        return self.policy


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


@pytest.mark.parametrize("proposal", (
    None, False, True, 0, 1, 3.14, [], {},
    Poison(), "", "  ", ".", "..",
    " safe.epub", "safe.epub ", "  safe.epub  ", "\u00a0safe.epub",
    "/tmp/PRIVATE_FILE.epub", "folder/safe.epub", "folder\\safe.epub",
    "safe.epub\nBearer PRIVATE_TOKEN", "x" * 513,
))
def test_invalid_filename_proposal_fails_one_row_without_comparison(proposal):
    ctx = Context(proposal)
    tool = NamingAuditTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert len(ctx.policy.calls) == 2
    assert plan.facts == {
        "checked": 0, "mismatch": 0, "unavailable": 0, "policy_failures": 2,
    }
    assert plan.warnings == (
        "Politique de nommage indisponible : notice n°1",
        "Politique de nommage indisponible : notice n°2",
        "2 entrée(s) sans proposition valide de la politique Core.",
        "Audit uniquement : aucune mutation de placement n'est effectuée.",
    )
    control = Control()
    result = tool.run(plan, control)
    assert not result.ok and result.changed == 0
    assert result.facts == plan.facts
    assert control.events == [{"message": plan.summary}]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for forbidden in ("PRIVATE", "Bearer", "/tmp/", "Exception", "Poison"):
        assert forbidden not in exported


@pytest.mark.parametrize("proposal,expected_mismatch", (
    ("safe.epub", 1),
    ("candidate.epub", 1),
    ("third.epub", 2),
    ("édition.epub", 2),
))
def test_real_filename_proposals_preserve_nonmutating_comparison(
    proposal, expected_mismatch,
):
    ctx = Context(proposal)
    plan = NamingAuditTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert plan.facts["checked"] == 2
    assert plan.facts["mismatch"] == expected_mismatch
    assert plan.facts["policy_failures"] == 0
    assert ctx.policy.calls == [
        {"title": "first", "author": "", "series": "", "index": "", "extension": "epub"},
        {"title": "second", "author": "", "series": "", "index": "", "extension": "epub"},
    ]


def test_one_invalid_preview_does_not_suppress_later_good_file():
    ctx = Context("safe.epub")
    proposals = iter((True, "candidate.epub"))
    ctx.policy.preview = lambda **_kw: next(proposals)
    plan = NamingAuditTool().activate(ctx).plan(MaintenanceContext(scope="library"))
    assert plan.facts == {
        "checked": 1, "mismatch": 0, "unavailable": 0, "policy_failures": 1,
    }
    assert plan.warnings[0] == "Politique de nommage indisponible : notice n°1"
