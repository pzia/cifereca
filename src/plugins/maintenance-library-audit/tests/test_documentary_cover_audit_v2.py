"""#3995: optional Core aggregate, separate N1/N2/N3, no legacy scope fallback."""
from copy import deepcopy
from dataclasses import replace

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool



class Control:
    def check_cancelled(self):
        pass

    def report(self, **data):
        pass


def facts():
    return {
        "schema": 2, "status": "partial", "entries": 3,
        "embedded": 1, "origin": 0, "catalog": 1, "ambiguous": 0,
        "missing": 0, "unknown": 1,
        "n2": {"embedded": 1, "origin": 0, "catalog": 1, "pending": 0,
               "ambiguous": 0, "missing": 0, "unknown": 1},
        "n3": {"digest_valid": 0, "signature_valid": 1, "not_validated": 1,
               "not_applicable": 0, "unknown": 1},
    }


class Broker:
    def __init__(self, value):
        self.value = value

    def catalog_entries(self, **kwargs):
        raise AssertionError("legacy catalogue must not be queried")

    def library_documentary_audit(self, **kwargs):
        raise AssertionError("v2 failure must not fall back")

    def library_cover_audit(self, *, identity_id):
        assert identity_id == "alice"
        return deepcopy(self.value)


class Context:
    def __init__(self, broker):
        self.catalog = broker

    def broker(self, name):
        assert name == "catalog"
        return self.catalog


def plan(value):
    return CoverAuditTool().activate(Context(Broker(value))).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )


def test_v2_preserves_separate_counts_and_partial_status():
    prepared = plan(facts())
    assert prepared.facts == facts()
    assert "N1 attesté" in prepared.summary and "N2 :" in prepared.summary
    assert "N3 :" in prepared.summary and "1 non validé" in prepared.summary
    result = CoverAuditTool().run(prepared, Control())
    assert result.ok and result.changed == 0
    assert result.facts == facts() and result.summary == prepared.summary


@pytest.mark.parametrize("level,key,value", [
    ("n2", "embedded", True),
    ("n2", "unknown", 2),
    ("n3", "not_validated", -1),
    ("n3", "signature_valid", 2),
])
def test_invalid_aggregate_never_returns_legacy_success(level, key, value):
    bad = facts()
    bad[level][key] = value
    assert plan(bad).facts["status"] == "unavailable"
    prepared = plan(facts())
    result = CoverAuditTool().run(replace(prepared, facts=bad), Control())
    assert not result.ok and result.changed == 0


def test_v1_fallback_only_when_optional_method_absent():
    legacy = {k: v for k, v in facts().items() if k not in ("n2", "n3")}
    legacy["schema"] = 1
    class Legacy:
        def library_documentary_audit(self, *, identity_id, kind):
            assert identity_id == "alice" and kind == "covers"
            return legacy
    prepared = CoverAuditTool().activate(Context(Legacy())).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )
    assert prepared.facts == legacy
    assert "non observées" in prepared.warnings[0]


def test_impossible_cross_level_counts_are_rejected():
    invalid = facts()
    invalid["n3"].update(digest_valid=2, not_validated=0, signature_valid=0)
    assert plan(invalid).facts["status"] == "unavailable"


@pytest.mark.parametrize("broken_port", [None, 42])
def test_declared_but_noncallable_v2_does_not_fall_back(broken_port):
    calls = []
    class Broken:
        library_cover_audit = broken_port

        def library_documentary_audit(self, **kwargs):
            calls.append("v1")
            legacy = {k: v for k, v in facts().items() if k not in ("n2", "n3")}
            legacy["schema"] = 1
            return legacy

        def catalog_entries(self, **kwargs):
            calls.append("catalogue")
            return []

    tool = CoverAuditTool().activate(Context(Broken()))
    prepared = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert prepared.facts["status"] == "unavailable"
    assert not tool.run(prepared, Control()).ok
    assert calls == []


def test_broken_port_lookup_is_unavailable_without_legacy_fallback():
    calls = []
    class Broken:
        @property
        def library_cover_audit(self):
            raise RuntimeError("private broker detail")

        def library_documentary_audit(self, **kwargs):
            calls.append("v1")
            return {}

    tool = CoverAuditTool().activate(Context(Broken()))
    prepared = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert prepared.facts["status"] == "unavailable"
    assert "private broker detail" not in repr(prepared)
    assert not tool.run(prepared, Control()).ok
    assert calls == []


def test_declared_but_noncallable_v1_does_not_use_catalogue():
    calls = []
    class Broken:
        library_documentary_audit = None

        def catalog_entries(self, **kwargs):
            calls.append("catalogue")
            return []

    tool = CoverAuditTool().activate(Context(Broken()))
    prepared = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert prepared.facts["status"] == "unavailable"
    assert calls == []
