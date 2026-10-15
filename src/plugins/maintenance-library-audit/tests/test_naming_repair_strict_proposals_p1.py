"""#1497: a naming preview must be a safe filename before it can plan mutation.

Synthetic Core brokers only: these tests do not write, rename or inspect files.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import NamingRepairTool
from tests.test_naming_repair_policy_failures_p1 import (
    Context, Control, _placement,
)


class Poison:
    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_POLICY_TOKEN")


@pytest.mark.parametrize("unsafe", (
    True, 123, Poison(),
    "/home/PRIVATE_ACCOUNT/new.epub",
    "folder/new.epub",
    r"C:\PRIVATE_ACCOUNT\new.epub",
    r"..\new.epub",
    ".", "..", " ",
    "PRIVATE\nnew.epub",
    "PRIVATE\x7fnew.epub",
    "x" * 513,
    "\u0000new.epub",
))
def test_invalid_proposal_is_quarantined_without_blocking_valid_placement(unsafe):
    context = Context([
        _placement("bad-preview", "epub"),
        _placement("good-preview", "pdf"),
    ])

    def preview(**options):
        return unsafe if options["extension"] == "epub" else "safe-new.pdf"

    context.naming.preview = preview
    tool = NamingRepairTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="current"))

    assert plan.facts == {
        "placement_ids": ("good-preview",),
        "skipped": 0,
        "policy_failures": 1,
        "read_failures": 0,
    }
    assert plan.changes == ("Renommer le placement n°1",)
    assert plan.warnings == (
        "1 placement(s) sans proposition valide de la politique Core.",
    )
    result = tool.run(plan, Control())
    assert result.changed == 1 and not result.ok
    assert [request.placement_id for request in context.maintenance.requests] == [
        "good-preview"
    ]
    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "preview": plan.summary, "changes": plan.changes,
    })
    assert "PRIVATE" not in public and "Bearer" not in public and "/home/" not in public


def test_rejected_absolute_proposal_never_counts_as_matching_basename():
    context = Context([_placement("only", "epub")])
    context.naming.preview = lambda **_opts: "/home/PRIVATE_ACCOUNT/PRIVATE_SOURCE_only.epub"
    plan = NamingRepairTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts["placement_ids"] == ()
    assert plan.facts["policy_failures"] == 1
    assert plan.changes == ()


def test_valid_simple_filename_still_plans_renaming():
    context = Context([_placement("only", "epub")])
    context.naming.preview = lambda **_opts: "safe-new.epub"
    plan = NamingRepairTool().activate(context).plan(
        MaintenanceContext(scope="library", identity_id="current")
    )
    assert plan.facts["placement_ids"] == ("only",)
    assert plan.facts["policy_failures"] == 0
