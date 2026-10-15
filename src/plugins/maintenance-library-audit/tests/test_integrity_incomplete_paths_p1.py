"""#1495: a skipped local path must never look like a complete integrity audit.

Synthetic maintenance plans only: no local book opened or provider contacted.
"""
from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import IntegrityAuditTool


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


def _run(*, candidates=(), missing_path=0):
    tool = IntegrityAuditTool()
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="PRIVATE_TITLE /home/PRIVATE_PERSON",
        warnings=("Bearer PRIVATE_TOKEN",),
        facts={"candidates": candidates, "missing_path": missing_path},
    )
    control = Control()
    return tool.run(plan, control), control.events


def test_skipped_local_path_alone_is_incomplete_without_fake_progress():
    result, events = _run(missing_path=1)
    assert result.ok is False
    assert result.changed == 0
    assert result.facts["checked"] == 0
    assert result.facts["missing_path"] == 1
    assert result.warnings == ("1 entrée(s) locale(s) sans chemin observable.",)
    assert "1 contrôle(s) indisponible(s)" in result.summary
    assert events == [
        {"message": "Aucun fichier local à vérifier"},
        {"message": "Audit d’intégrité incomplet : 1 contrôle(s) indisponible(s)"},
    ]
    assert "PRIVATE" not in str(result) + str(events)


def test_valid_inspected_file_does_not_hide_an_unobservable_local_row(monkeypatch):
    import cifereca_library_audits as audits

    inspected = []
    def inspect(path, fmt, *, expected_size, expected_sha256):
        inspected.append((path, fmt, expected_size, expected_sha256))
        return ()
    monkeypatch.setattr(audits, "_file_integrity_issues", inspect)

    candidate = {
        "path": "/synthetic/fictional.pdf",
        "format": "pdf",
        "expected_size": 2,
        "expected_sha256": "",
    }
    result, events = _run(candidates=(candidate,), missing_path=2)
    assert result.ok is False
    assert result.facts["checked"] == 1
    assert result.facts["invalid"] == 0
    assert result.facts["missing_path"] == 2
    assert inspected == [(candidate["path"], "pdf", 2, "")]
    assert events == [
        {"current": 0, "total": 1, "message": "Intégrité 1/1"},
        {"current": 1, "total": 1,
         "message": "Audit d’intégrité incomplet : 0 anomalie(s), 2 contrôle(s) indisponible(s)"},
    ]
