"""#3252/1A: a readable EPUB that remains content-protected has a distinct
closed terminal reason, without becoming a Canonical CompletedDelivery.

Real synthetic EPUB ZIPs, SQLite, and the actual Core offer/consent/receipt
path. Runtime callbacks are local fakes; no provider access or DRM bypass.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from zipfile import ZipFile

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.downloadable_resources import (
    DeliveryCandidate, SourceReference,
)
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_selected_candidate_finalization import _prepare
from tests.test_core_p0_3252_real_epub_final_veto import _epub_bytes


@pytest.fixture(autouse=True)
def _acquisition_lock(monkeypatch, tmp_path):
    lock = lambda: tmp_path / "state" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file", lock,
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_offers.acquisition_lock_file", lock,
    )


def _candidate_source(service):
    source = service.source
    return SourceReference(
        source.scope, source.branch, source.edition_id, source.right_id,
        source.kind,
    )


def _protected_epub(variant):
    payload = _epub_bytes(encrypted=(variant != "clear"))
    if variant != "adept":
        return payload
    buffer = io.BytesIO()
    with ZipFile(io.BytesIO(payload)) as source, ZipFile(buffer, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "META-INF/encryption.xml":
                content = (
                    b"<encryption><EncryptedData>"
                    b"<EncryptionMethod Algorithm='adobe.com/adept'/>"
                    b"</EncryptedData></encryption>"
                )
            target.writestr(item, content)
    return buffer.getvalue()


@pytest.mark.parametrize(("route", "marker"), [
    ("runtime", "aes"),
    ("runtime", "adept"),
    ("direct", "aes"),
], ids=["kdrm-runtime-aes", "kdrm-runtime-adept", "https-direct-aes"])
def test_protected_epub_after_selected_delivery_reports_drm_unresolved(
    rig, caplog, route, marker,
):
    service, db, root = rig
    _prepare(service)
    content = _protected_epub(marker)
    calls = []

    if route == "runtime":
        service.session.downloadable_selected_delivery_requires_runtime = (
            lambda *_args, **_kwargs: True
        )

        def guarded(_account, *, admission_current, execute_runtime,
                    target_dir, selected_offer_id, **_kwargs):
            assert admission_current() is True
            calls.append("runtime")
            target = Path(target_dir) / "candidate.epub"

            def fake_runtime(request):
                assert request["destination"] == str(target)
                target.write_bytes(content)
                return {"ok": True, "artifact": {"path": str(target)}}

            service.execute_acquisition_request = fake_runtime
            execute_runtime({
                "schema_version": 2,
                "source": {"type": "kobo", "provider": "synthetic"},
                "format": "epub", "destination": str(target),
            })
            return DeliveryCandidate(
                _candidate_source(service), selected_offer_id, target,
            )

        service.session.downloadable_selected_delivery_candidate_runtime_guarded = guarded
    else:
        def guarded(_account, *, admission_current, target_dir,
                    selected_offer_id, **_kwargs):
            assert admission_current() is True
            calls.append("direct")
            target = Path(target_dir) / "candidate.epub"
            target.write_bytes(content)
            return DeliveryCandidate(
                _candidate_source(service), selected_offer_id, target,
            )

        service.session.downloadable_selected_delivery_candidate_guarded = guarded

    with caplog.at_level(logging.INFO, logger="cifereca"):
        outcome = synchronize_account_source(service, service.account)
    execution = outcome["canonical_execution"]
    assert execution["failed"] == 1
    assert execution["downloaded"] == execution["recovered"] == 0
    assert len(calls) == 1
    assert refs(db)[1] == 0
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    terminal = [
        row.getMessage() for row in caplog.records
        if "TRACE canonical_flow " in row.getMessage()
        and "phase=terminal " in row.getMessage()
    ]
    assert len(terminal) == 1
    assert "status=failed" in terminal[0]
    assert "reason=drm_unresolved" in terminal[0]
    assert not any(
        "reason=canonical_delivery_failed" in x for x in terminal
    )
    rejected = [
        row.getMessage() for row in caplog.records
        if row.getMessage().startswith("TRACE canonical delivery final rejected ")
    ]
    assert len(rejected) == 1
    assert "code=canonical_delivery_final_invalid" in rejected[0]
    assert "inspection_protected=True" in rejected[0]


def test_transport_failure_is_not_misreported_as_drm_unresolved(rig, caplog):
    service, db, root = rig
    _prepare(service)

    def broken_download(_account, *, admission_current, **_kwargs):
        assert admission_current() is True
        raise OSError("synthetic transport refused")

    service.session.downloadable_selected_delivery_candidate_guarded = (
        broken_download
    )
    with caplog.at_level(logging.INFO, logger="cifereca"):
        outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0 and db.placements() == []
    terminal = [
        row.getMessage() for row in caplog.records
        if "TRACE canonical_flow " in row.getMessage()
        and "phase=terminal " in row.getMessage()
    ]
    assert len(terminal) == 1
    assert "reason=canonical_delivery_failed" in terminal[0]
    assert "reason=drm_unresolved" not in terminal[0]
