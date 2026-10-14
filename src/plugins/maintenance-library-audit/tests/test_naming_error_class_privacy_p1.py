"""#1497: naming repair never exports a private exception class name.

Offline mutation broker only; no renaming, filesystem writes or GTK.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **event):
        self.events.append(event)


@pytest.mark.parametrize("stage", ("plan", "run"))
def test_naming_broker_exception_name_and_body_are_both_redacted(stage):
    SecretFailure = type("Bearer_PRIVATE_ACCESS_TOKEN_PATH", (OSError,), {})

    class Broker:
        def __init__(self):
            self.requests = []

        def plan(self, request):
            self.requests.append(request)
            if stage == "plan" and request.placement_id == "PRIVATE_PLACEMENT":
                raise SecretFailure("PRIVATE_PASSWORD /home/PRIVATE_OWNER/a.epub")
            return request

        def run(self, request, control):
            if stage == "run" and request.placement_id == "PRIVATE_PLACEMENT":
                raise SecretFailure("PRIVATE_PASSWORD /home/PRIVATE_OWNER/a.epub")
            return LibraryMutationResult(
                True, changed=1, summary="PRIVATE_CORE_SUMMARY",
            )

    class Context:
        def __init__(self, broker):
            self.maintenance = broker

        def broker(self, name):
            assert name == "library.maintenance"
            return self.maintenance

    broker = Broker()
    tool = NamingRepairTool().activate(Context(broker))
    plan = MaintenancePlan(tool_id=tool.info.id, summary="Prévision",
                           facts={"placement_ids": (
                               "PRIVATE_PLACEMENT", "safe-placement",
                           )})
    control = Control()
    result = tool.run(plan, control)
    assert result.ok is False and result.changed == 1
    assert result.facts == {"planned": 2, "failures": 1}
    assert result.warnings == ("Renormalisation 1/2 : échec du service",)
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 2), (1, 2), (2, 2)]
    assert "échec" in control.events[-1]["message"]
    assert control.checks >= 4
    assert [request.placement_id for request in broker.requests] == [
        "PRIVATE_PLACEMENT", "safe-placement",
    ]
    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "PASSWORD", "/home/", "PATH", "OSError"):
        assert secret not in public
