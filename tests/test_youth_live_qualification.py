"""No-network regressions for the explicitly opt-in youth HTTPS check."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS


def script_module():
    path = Path(__file__).resolve().parents[1] / "scripts/qualify-youth-source-live.py"
    spec = importlib.util.spec_from_file_location("live_youth_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_never_imports_core_or_attempts_live_https(capsys):
    script = script_module()
    assert script.main(["--source", "booktrust"]) == 2
    assert '"result": "NOT_RUN"' in capsys.readouterr().out


def test_input_limits_and_hard_request_budget(capsys):
    script = script_module()
    for argv in (
        ["--source", "milan", "--age", "18", "--confirm-live"],
        ["--source", "milan", "--age", "-1", "--confirm-live"],
        ["--source", "milan", "--limit", "4", "--confirm-live"],
        ["--source", "booktrust", "--timeout", "30", "--confirm-live"],
    ):
        assert script.main(argv) == 2
    assert "INVALID_INPUT" in capsys.readouterr().out
    assert script.plan(script.parser().parse_args(["--source", "milan"]))["request_budget"] == 9
    assert script.plan(script.parser().parse_args(["--source", "booktrust"]))["request_budget"] == 1


def test_evidence_requires_real_source_label_and_origin():
    script = script_module()
    age = NS(source_id="milan", age_kind="publisher", source_label="À partir de 8 ans",
             age_min=8, age_max=None)
    book = dict(title="Lumilane", identifiers={"isbn": "9782408060381"},
                age_ranges=(age,), notice_url="https://www.editionsmilan.com/livres/97384-lumilane/",
                provenance=("milan",))
    assert len(script.evidence_for((NS(**book),), "milan")) == 1
    assert not script.evidence_for((NS(**(book | {"provenance": ()})),), "milan")
    assert not script.evidence_for((NS(**(book | {"provenance": ("booktrust",)})),), "milan")
    assert not script.evidence_for((NS(**(book | {"notice_url": "https://evil.example/"})),), "milan")
    assert not script.evidence_for((NS(**(book | {"identifiers": {}})),), "milan")
    wrong = NS(**(vars(age) | {"source_label": ""}))
    assert not script.evidence_for((NS(**(book | {"age_ranges": (wrong,)})),), "milan")
    interest = NS(source_id="booktrust", age_kind="interest", source_label="Interest age 7-9",
                  age_min=7, age_max=9)
    bt = NS(title="BookTrust title", identifiers={}, age_ranges=(interest,),
            notice_url="https://www.booktrust.org.uk/book-recommendations/bookfinder/example/",
            provenance=("booktrust",))
    assert len(script.evidence_for((bt,), "booktrust")) == 1


def test_live_qualification_requires_an_observed_https_request():
    script = script_module()
    qualified = [{"title": "Example", "provenance": ["milan"]}]
    assert script.qualification_status([], qualified) == "BLOCKED"
    assert script.qualification_status(["https://www.editionsmilan.com/livres/"], []) == "NO_VERIFIED_NOTICE"
    assert script.qualification_status(["https://www.editionsmilan.com/livres/"], qualified) == "PASS"
