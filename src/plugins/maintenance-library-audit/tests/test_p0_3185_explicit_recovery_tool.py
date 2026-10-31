"""#3185: the recovery tool never treats a saved queue as unlink consent."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenanceContext
from cifereca_dedup_recovery import CanonicalDedupRecoveryTool


class Control:
    def __init__(self):
        self.events = []
        self.cancelled = False

    def check_cancelled(self):
        if self.cancelled:
            raise RuntimeError("cancelled")

    def report(self, **kwargs):
        self.events.append(kwargs)


class Broker:
    def __init__(self):
        self.rows = [("candidate-a", "keeper-a", "A")]
        self.review_status = "reviewed"
        self.review_unverified = 0
        self.requested = []
        self.run_count = 0
        self.result_status = "retired"
        self.changed = 1

    def pending_canonical_cleanups(self, *, identity_id=None):
        if self.review_status == "unavailable":
            return SimpleNamespace(
                status="unavailable", unverified_count=0, candidates=(),
            )
        candidates = [
            SimpleNamespace(
                candidate_placement_id=candidate,
                keeper_placement_id=keeper,
                identity_id=identity,
            ) for candidate, keeper, identity in self.rows
            if identity_id is None or identity_id == identity
        ]
        return SimpleNamespace(
            status=self.review_status, unverified_count=self.review_unverified,
            candidates=tuple(candidates),
        )

    def plan(self, request):
        self.requested.append(request)
        assert request.operation == "canonical_dedup"
        assert request.options == {"action": "recover", "confirmed": True}
        return request

    def run(self, request, control):
        self.run_count += 1
        return LibraryMutationResult(
            self.result_status == "retired", self.changed, "PRIVATE_SOURCE",
            facts={"status": self.result_status},
        )


class PluginContext:
    def __init__(self, broker):
        self.target = broker

    def broker(self, name):
        assert name == "library.maintenance"
        return self.target


def rig():
    broker = Broker()
    return CanonicalDedupRecoveryTool().activate(PluginContext(broker)), broker


def test_recovery_preview_is_non_mutating_and_identity_scoped():
    tool, broker = rig()
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    assert plan.tool_id == "library.duplicates.recover"
    assert plan.facts == {
        "review_status": "reviewed", "identity_id": "A",
        "pairs": (("candidate-a", "keeper-a", "A"),),
    }
    assert plan.changes == ("Vérifier et reprendre le nettoyage n°1",)
    assert not broker.requested and not broker.run_count
    assert tool.info.danger_level == "destructive"


def test_confirmed_execution_only_uses_core_recover_and_replans():
    tool, broker = rig()
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    control = Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 1
    assert result.facts == {"attempted": 1, "completed": 1, "failures": 0}
    assert len(broker.requested) == broker.run_count == 1
    assert broker.requested[0].placement_id == "candidate-a"
    assert broker.requested[0].keep_placement_id == "keeper-a"
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 1), (1, 1)]
    assert "PRIVATE" not in str(result)


def test_stale_preview_is_rejected_before_mutation():
    tool, broker = rig()
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    broker.rows = [("candidate-a", "keeper-changed", "A")]
    result = tool.run(plan, Control())
    assert not result.ok and result.changed == 0
    assert result.facts == {"attempted": 0, "failures": 1}
    assert broker.requested == []


@pytest.mark.parametrize("status,unverified", [
    ("partial", 1), ("unavailable", 0), ("over_budget", 0),
])
def test_partial_or_unknown_queue_is_not_an_authorization(status, unverified):
    tool, broker = rig()
    broker.review_status = status
    broker.review_unverified = unverified
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    assert plan.facts["pairs"] == ()
    assert plan.facts["review_status"] == "unavailable"
    assert not tool.run(plan, Control()).ok
    assert broker.run_count == 0


@pytest.mark.parametrize("bad", [
    (("same", "same", "A"),),
    (("candidate-a", "keeper-a", "B"),),
    (("candidate-a", "keeper-a", "A"), ("candidate-b", "candidate-a", "A")),
    (("candidate-a", "keeper-a", "A"), ("candidate-a", "keeper-b", "A")),
    (("candidate-a\n", "keeper-a", "A"),),
])
def test_invalid_queue_or_foreign_identity_never_runs_core(bad):
    tool, broker = rig()
    broker.rows = list(bad)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    if len(bad) == 1 and bad[0][2] == "B":
        assert plan.facts["pairs"] == ()
    else:
        assert plan.facts["review_status"] == "unavailable"
    assert not broker.requested
    assert broker.run_count == 0


@pytest.mark.parametrize("patch", [
    {"tool_id": "untrusted.tool"},
    {"facts": {"pairs": (("candidate-a", "keeper-a", "A"),)}},
    {"facts": {"review_status": "reviewed", "identity_id": "A",
               "pairs": (("candidate-a", "keeper-a", "A"),
                         ("candidate-a", "other", "A"))}},
    {"facts": {"review_status": "reviewed", "identity_id": "A",
               "pairs": (("candidate-a", "keeper-a", "B"),)}},
])
def test_forged_plan_does_not_touch_broker(patch):
    tool, broker = rig()
    original = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    forged = replace(original, **patch)
    result = tool.run(forged, Control())
    assert not result.ok and result.changed == 0
    assert not broker.requested


def test_partial_core_result_never_claims_success_or_exposes_data():
    tool, broker = rig()
    broker.result_status = "cleanup_pending"
    broker.changed = 0
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
    result = tool.run(plan, Control())
    assert not result.ok and result.changed == 0
    assert result.facts["failures"] == 1
    assert "PRIVATE" not in str(result)


def test_successful_resume_after_unlink_but_before_sql_cleanup_can_change_zero_files():
    tool, broker = rig()
    broker.changed = 0
    result = tool.run(
        tool.plan(MaintenanceContext(scope="library", identity_id="A")),
        Control(),
    )
    assert result.ok
    assert result.changed == 0
    assert result.facts["completed"] == 1
