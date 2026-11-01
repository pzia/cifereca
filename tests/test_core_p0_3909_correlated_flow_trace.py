"""P0 #3909: per-run closed Core flow phases, including post-staging recovery.

Only a synthetic candidate and real SQLite/FS Core publication; no supplier GET.
"""
from __future__ import annotations

import logging
import os
import re

from cifereca.core import canonical_promotion
from cifereca.core.canonical_flow_trace import CanonicalFlowTrace
from cifereca.core.source_convergence import SourceKey
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api import ProviderRightsScope
from tests.test_core_p0_3909_atomic_promotion import (
    rig, _isolate_delivery_lock, _drive,
)


TRACE = re.compile(
    r"^TRACE canonical_flow run_ref=([0-9a-f]{24}) "
    r"item_ref=([0-9a-f]{24}) "
    r"phase=([a-z_]+) code=([a-z_]+) "
    r"reason=([a-z_]+) kind=(purchase|loan)$"
)


def _events(caplog):
    observed = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("TRACE canonical_flow ")
    ]
    assert observed
    parsed = []
    for line in observed:
        hit = TRACE.fullmatch(line)
        assert hit is not None, line
        parsed.append(dict(
            run=hit[1], item=hit[2], phase=hit[3], code=hit[4],
            reason=hit[5], kind=hit[6],
        ))
    return parsed, observed


def test_download_receipt_promotion_publish_then_presence_share_one_ephemeral_key(
    rig, caplog,
):
    service, db, root, calls = _drive(rig)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        first = synchronize_account_source(service, service.account)
        second = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert second["canonical_execution"]["present_attested"] == 1
    assert calls == ["signed-right-offer"]
    assert len(db.placements()) == 1

    rows, lines = _events(caplog)
    runs = list(dict.fromkeys(row["run"] for row in rows))
    assert len(runs) == 2
    initial = [row for row in rows if row["run"] == runs[0]]
    later = [row for row in rows if row["run"] == runs[1]]
    assert len({row["item"] for row in initial}) == 1
    assert len({row["item"] for row in later}) == 1
    assert initial[0]["item"] != later[0]["item"]  # no cross-run tracking
    steps = [(row["phase"], row["code"]) for row in initial]
    for item in (
        ("observed", "ok"), ("recovery", "miss"), ("delivery", "start"),
        ("delivery", "ok"), ("receipt", "ok"), ("promotion", "ok"),
        ("publication", "ok"), ("terminal", "downloaded"),
    ):
        assert item in steps
    assert later[-1]["phase"] == "terminal"
    assert later[-1]["code"] == "present_attested"
    assert later[-1]["reason"] == "exact_verified_origin"
    assert all(row["kind"] == "purchase" for row in rows)
    rendered = "\n".join(lines)
    for secret in ("signed-right-offer", "private.invalid", "purchase",
                   "credential-synthetic", "received.pdf"):
        # kind=purchase is a safe enum, so assert only the input identifiers
        if secret != "purchase":
            assert secret not in rendered


def test_failed_promotion_is_phase_identifiable_and_recovered_without_second_get(
    rig, caplog, monkeypatch,
):
    service, db, root, calls = _drive(rig)
    real_link = os.link
    faults = []

    def fail_once(source, target, **kwargs):
        if str(source).startswith(".canonical-promote-") and not faults:
            faults.append(True)
            raise OSError("PRIVATE-URL?csrf=SENSITIVE-synthetic")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(canonical_promotion.os, "link", fail_once)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        failed = synchronize_account_source(service, service.account)
        recovered = synchronize_account_source(service, service.account)
    assert failed["canonical_execution"]["failed"] == 1
    assert recovered["canonical_execution"]["recovered"] == 1
    assert len(db.placements()) == 1
    assert calls == ["signed-right-offer"]

    rows, lines = _events(caplog)
    runs = list(dict.fromkeys(row["run"] for row in rows))
    assert len(runs) == 2
    first = [row for row in rows if row["run"] == runs[0]]
    second = [row for row in rows if row["run"] == runs[1]]
    assert ("promotion", "failed") in [(r["phase"], r["code"]) for r in first]
    assert first[-1]["phase"] == "terminal"
    assert first[-1]["code"] == "failed"
    assert first[-1]["reason"] == "canonical_promotion_failed"
    assert ("recovery", "ok") in [(r["phase"], r["code"]) for r in second]
    assert not any(r["phase"] == "delivery" for r in second)
    assert second[-1]["code"] == "recovered"
    assert second[-1]["reason"] == "canonical_delivery_recovered"
    assert "SENSITIVE-synthetic" not in "\n".join(lines)


def test_trace_ref_is_salted_closed_and_never_logs_provider_payload(caplog):
    trace = CanonicalFlowTrace()
    source = SourceKey(
        ProviderRightsScope(
            "PRIVATE-ID-NEVER-LOG", "private.example", "CARD-SECRET",
            "BORROWER-SECRET",
        ),
        "EDITION-SECRET", "OFFER-SECRET", "purchase", "purchases",
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        trace.emit(source, "delivery", "start")
        trace.emit(source, "terminal", "blocked", reason="consent_unconfigured")
        trace.emit(source, "terminal", "failed",
                   reason="sensitive?secret=PRIVATE-URL")
        trace.emit(source, "malicious\ncredential=SECRET", "start")
        trace.emit(source, "delivery", "post_executed")
    rows, lines = _events(caplog)
    assert len(rows) == 3  # unknown phase/code never emitted
    assert rows[0]["item"] == rows[1]["item"] == rows[2]["item"]
    assert rows[1]["reason"] == "consent_unconfigured"
    assert rows[2]["reason"] == "none"
    all_logs = "\n".join(lines)
    for private in (
        "PRIVATE-ID-NEVER-LOG", "CARD-SECRET", "BORROWER-SECRET",
        "EDITION-SECRET", "OFFER-SECRET", "PRIVATE-URL",
        "private.example", "malicious",
    ):
        assert private not in all_logs
    assert CanonicalFlowTrace().item_ref(source) != trace.item_ref(source)


def test_trace_sink_failure_is_non_blocking(monkeypatch):
    trace = CanonicalFlowTrace()
    source = SourceKey(
        ProviderRightsScope("I", "P", "C", "A"),
        "E", "R", "loan", "loans",
    )
    def broken(*args, **kwargs):
        raise RuntimeError("logger not available")
    monkeypatch.setattr("cifereca.core.canonical_flow_trace._LOG.info", broken)
    trace.emit(source, "receipt", "start")  # no crash, no grant
