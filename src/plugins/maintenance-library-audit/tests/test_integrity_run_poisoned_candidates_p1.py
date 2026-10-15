"""#1495: untrusted Mapping accessors are quarantined per runtime candidate."""
import json

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import IntegrityAuditTool


class PoisonedGet(dict):
    def __init__(self, poisoned_field):
        super().__init__(
            path="/home/PRIVATE_ACCOUNT/secret.pdf",
            format="pdf", expected_size=1, expected_sha256="",
        )
        self.poisoned_field = poisoned_field

    def get(self, key, default=None):
        if key == self.poisoned_field:
            SecretException = type("Bearer_PRIVATE_TOKEN_EXCEPTION", (RuntimeError,), {})
            raise SecretException("/home/PRIVATE_ACCOUNT/secret.pdf")
        return super().get(key, default)


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


@pytest.mark.parametrize("field", (
    "expected_size", "path", "format", "expected_sha256",
))
def test_poisoned_get_only_rejects_that_candidate(field, monkeypatch):
    calls = []
    import cifereca_library_audits as audits

    monkeypatch.setattr(
        audits, "_file_integrity_issues",
        lambda path, fmt, **kw: calls.append((path, fmt, kw)) or (),
    )
    plan = MaintenancePlan(
        tool_id=IntegrityAuditTool.info.id,
        summary="Bearer PRIVATE_PLAN_TITLE",
        warnings=("PRIVATE_PATH",),
        facts={"candidates": (
            PoisonedGet(field),
            {
                "path": "/safe/fixture.pdf", "format": "pdf",
                "expected_size": 0, "expected_sha256": "",
            },
        )},
    )
    control = Control()
    result = IntegrityAuditTool().run(plan, control)
    assert not result.ok
    assert result.facts["checked"] == 2
    assert result.facts["invalid"] == 1
    assert result.facts["issues"] == {"reference_invalid": 1}
    assert calls == [
        ("/safe/fixture.pdf", "pdf", {"expected_size": 0, "expected_sha256": ""}),
    ]
    assert [
        (event.get("current"), event.get("total")) for event in control.events
    ] == [(0, 2), (1, 2), (2, 2)]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    })
    for secret in ("PRIVATE", "Bearer", "/home/", "EXCEPTION"):
        assert secret not in exported
