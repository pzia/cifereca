"""Positive and negative witnesses for advisory test-quality triage."""
from scripts.ci_test_quality import analyze, new_findings


def test_signals_and_distinct_valid_contracts():
    report = analyze("tests/test_example.py", """
import pytest
def test_constant():
    assert True
def test_reflexive(value):
    assert value == value
def test_weak(service):
    assert service.lookup() is not None
def test_mock(m):
    m.return_value = 5
    assert m() == 5
@pytest.mark.skip(reason="legacy")
def test_skipped():
    assert False
def test_valid(service):
    assert service.lookup() == 5
def test_negative(service):
    with pytest.raises(ValueError):
        service.lookup()
def test_two_checks(service):
    value = service.lookup()
    assert value is not None
    assert value == 5
def test_helper_only():
    def helper():
        assert True
    helper()
class TestContract:
    def test_self(self):
        self.assertEqual(4, 4)
""")
    observed = {(row["test"], row["kind"]) for row in report["findings"]}
    assert observed == {
        ("test_constant", "constant_truthy_assertion"),
        ("test_reflexive", "reflexive_comparison"),
        ("test_weak", "sole_weak_shape_assertion"),
        ("test_mock", "configured_return_assertion"),
        ("test_skipped", "skip_or_xfail_review"),
        ("test_helper_only", "no_recognized_assertion"),
        ("TestContract.test_self", "reflexive_comparison"),
    }


def test_ast_fingerprint_keeps_parameters_decorators_and_values():
    report = analyze("tests/test_example.py", """
import pytest
def test_first(service):
    assert service.read() == 5
def test_second(service):
    assert service.read() == 5
def test_different(service):
    assert service.read() == 6
@pytest.mark.parametrize("service", [1])
def test_param(service):
    assert service.read() == 5
""")
    first, second, different, param = [r["sha256"] for r in report["fingerprints"]]
    assert first == second
    assert len({first, different, param}) == 3


def test_delta_ignores_line_shifts_but_keeps_extra_occurrences():
    old = {"path": "tests/test_a.py", "test": "test_a", "kind": "signal", "line": 1}
    moved = dict(old, line=5)
    extra = dict(old, line=6)
    assert new_findings([old], [moved, extra]) == [extra]
    assert new_findings([old], [moved]) == []


def test_delegated_oracles_are_distinguished_without_hiding_candidates():
    report = analyze("tests/test_example.py", """
import pytest
def check(value):
    assert value == 7
def reject(service):
    with pytest.raises(ValueError):
        service.run()
def noop(value):
    return value
def tautology(value):
    assert value == value
def test_valid(service):
    check(service.run())
def test_rejection(service):
    reject(service)
def test_empty(service):
    noop(service)
def test_tautology(service):
    tautology(service)
def test_shadowed(check):
    check(7)
def test_conditional(service):
    if False:
        check(service.run())
""")
    observed = {r["test"]: r["kind"] for r in report["findings"]}
    assert observed == {
        "test_valid": "delegated_oracle_review",
        "test_rejection": "delegated_oracle_review",
        "test_empty": "no_recognized_assertion",
        "test_tautology": "no_recognized_assertion",
        "test_shadowed": "no_recognized_assertion",
        "test_conditional": "no_recognized_assertion",
    }
