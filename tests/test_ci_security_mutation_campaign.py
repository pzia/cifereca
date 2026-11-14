"""Executable mutation proof for two critical public-peer protections (#5376).

Only the two dedicated test functions are rerun in child pytest processes,
on fabricated DNS/socket data. Never performs outbound network requests.
"""
from __future__ import annotations

import pytest

from scripts.ci_security_mutation_campaign import MUTATIONS, campaign


def test_mutations_are_independent_and_have_explicit_behavioral_witnesses():
    assert len(MUTATIONS) == 2
    assert len({m.name for m in MUTATIONS}) == 2
    assert all(m.test_node.startswith("tests/test_public_httpx_transport.py::") for m in MUTATIONS)
    assert all(m.anchor != m.replacement for m in MUTATIONS)


def test_bounded_security_mutants_are_killed_on_real_transport_tests():
    report = campaign(timeout=40)
    assert report["passed"], report
    assert all(p["baseline_exit"] == 0 for p in report["probes"])
    assert all(p["mutant_exit"] == 1 for p in report["probes"])


def test_mutation_baseline_failure_never_counts_as_killed(monkeypatch, tmp_path):
    from scripts import ci_security_mutation_campaign as probe
    class Result:
        returncode = 1
        stdout = "FAILED baseline"
        stderr = ""
    monkeypatch.setattr(probe, "_run_pytest", lambda *args, **kwargs: Result())
    source = tmp_path / probe.TARGET.relative_to(probe.ROOT)
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(m.anchor for m in MUTATIONS), encoding="utf-8")
    with pytest.raises(RuntimeError, match="baseline_failed"):
        campaign(tmp_path)
