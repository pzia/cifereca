"""Mutations on Core public peers and revoked delivery admission (#5376).

All witness tests run with synthetic DNS, HTTPX and temporary FS only.
No real provider connection is made.
"""
from __future__ import annotations

import pytest

from scripts.ci_security_mutation_campaign import MUTATIONS, campaign


def test_mutations_are_independent_and_have_explicit_behavioral_witnesses():
    assert len(MUTATIONS) == 3
    assert len({m.name for m in MUTATIONS}) == 3
    assert all(m.test_node.startswith("tests/test_public_httpx_transport.py::")
               for m in MUTATIONS[:2])
    consent = MUTATIONS[2]
    assert consent.target_module == "cifereca.core.attested_https_delivery"
    assert consent.target_function == "_admitted"
    assert consent.test_node.endswith("test_admission_revoked_after_redirect_denies_followup_before_get")
    assert len(consent.additional_test_nodes) == 1
    assert consent.additional_test_nodes[0].endswith(
        "test_rejected_final_publish_after_stream_preserves_no_target"
    )
    assert all(m.anchor != m.replacement for m in MUTATIONS)


def test_bounded_security_mutants_are_killed_on_real_transport_and_admission_tests():
    report = campaign(timeout=40)
    assert report["passed"], report
    assert report["distinct_mutation_sites"] == 3
    assert report["witnesses"] == 4
    assert all(p["baseline_exit"] == 0 for p in report["probes"])
    assert all(p["mutant_exit"] == 1 for p in report["probes"])
    assert sum(p["mutation"] == "respect_revoked_delivery_admission"
               for p in report["probes"]) == 2
    assert all("attested_https_delivery.py::_admitted" in p["subject"]
               for p in report["probes"] if p["mutation"] == "respect_revoked_delivery_admission")


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
