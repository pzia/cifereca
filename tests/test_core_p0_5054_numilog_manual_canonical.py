"""#5054: explicit Numilog Library click uses canonical writer, not v1 receipt."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.plugin_api.downloadable_resources import (
    DownloadCapability, ResourceObservation, ResourceSnapshot, SourceReference,
)
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope
from cifereca.core.numilog_owned_download import NumilogOwnedDownloadTarget
import cifereca.core.numilog_manual_canonical as manual


def _setup(monkeypatch, tmp_path, *, mismatch=False, rebind=False):
    scope = ProviderRightsScope("owner", "numilog", "credential", "account")
    account = Account(provider_id="numilog", credential_id="credential",
                      unique_id="account", identity_id="owner")
    selected = ResourceSnapshot(
        scope=scope, branch="meslivres", status="success", coverage="partial",
        resources=(ResourceObservation(
            source=SourceReference(scope, "meslivres", "edition", 
                                   "other" if mismatch else "edition", "purchase"),
            capability=DownloadCapability("available", "offer:test"),
            evidence_ref="evidence:test", observed_at="2026-11-10T10:00:00Z",
        ),),
    )
    session = NS(connected=True, provider_id="numilog", credential_id="credential",
                 accounts=[account], _closed=False)
    calls = []
    def offer(_account, **kwargs):
        calls.append(("offer", kwargs))
        if rebind:
            service._source_context_epoch += 1
        return selected
    session.downloadable_selected_offer_snapshot = offer
    session.download_direct = lambda *_a, **_k: pytest.fail("legacy direct GET prohibited")
    target = NumilogOwnedDownloadTarget(
        "right", "owner", "source", "credential", "account", "edition",
        ("/lib", ("/lib/owner",)), id(session), session,
    )
    stored_path = tmp_path / "book.epub"
    stored_path.write_bytes(b"fixture")
    storage = NS(placements=lambda: [
        dict(identity_id="owner", provider_id="numilog", artifact_id="sha256:result",
             state="present", source_item_id="edition", placement_id="placement")])
    service = NS(
        accounts=[account], _sessions={("numilog", "credential"): session},
        storage=storage, _source_context_epoch=1,
        _session_key=lambda *x: tuple(x), library_dir=lambda: tmp_path,
        local_book_path=lambda *_a, **_k: stored_path,
    )
    monkeypatch.setattr("cifereca.core.numilog_owned_download._resolve",
                        lambda *_a, **_k: (target, NS(record_id="edition")))
    class FakeLock:
        def __init__(self, *_a): pass
        def __enter__(self): return self
        def __exit__(self, *_a): pass
    monkeypatch.setattr(manual, "InterProcessLock", FakeLock)
    monkeypatch.setattr(manual, "acquisition_lock_file", lambda: tmp_path / "lock")
    monkeypatch.setattr(manual, "has_complete_delivery_abi", lambda *_a: True)
    monkeypatch.setattr(manual, "persist_attested_source_references",
                        lambda *_a, **_k: calls.append(("persist", None)))
    def execute(_service, _account, _session, snapshot, **kwargs):
        calls.append(("execute", kwargs))
        assert len(snapshot.rights) == 1
        assert kwargs["persisted_consent"] == frozenset({
            manual.SourceKey(scope, "edition", "edition", "purchase", "meslivres")
        })
        assert kwargs["consent_still_current"](
            next(iter(kwargs["persisted_consent"]))
        )
        assert kwargs.get("expected_source_policy_revision") is None
        return [NS(source=next(iter(kwargs["persisted_consent"])),
                   status="downloaded", artifact_id="sha256:result")]
    monkeypatch.setattr(manual, "execute_admitted_convergence", execute)
    return service, target, calls


def test_one_click_uses_one_selected_offer_and_canonical_executor(monkeypatch, tmp_path):
    service, target, calls = _setup(monkeypatch, tmp_path)
    result = manual.obtain_explicit_numilog(service, target)
    assert result.artifact_id == "sha256:result"
    assert result.identity_id == "owner"
    assert result.path == tmp_path / "book.epub"
    assert [x[0] for x in calls] == ["offer", "persist", "execute"]
    assert calls[0][1] == dict(
        edition_id="edition", resource_id="edition", kind="purchase",
        branch="meslivres", explicit_user_action=True,
    )


@pytest.mark.parametrize("damage", ["offer_mismatch", "epoch_rebound"])
def test_no_canonical_writer_after_unattested_offer_or_context_change(
    monkeypatch, tmp_path, damage,
):
    service, target, calls = _setup(
        monkeypatch, tmp_path, mismatch=damage == "offer_mismatch",
        rebind=damage == "epoch_rebound",
    )
    with pytest.raises(ValueError, match="explicit_canonical"):
        manual.obtain_explicit_numilog(service, target)
    assert [x[0] for x in calls] == ["offer"]


def test_no_legacy_fallback_when_guarded_abi_unavailable(monkeypatch, tmp_path):
    service, target, calls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(manual, "has_complete_delivery_abi", lambda *_a: False)
    with pytest.raises(ValueError, match="explicit_canonical_abi_unavailable"):
        manual.obtain_explicit_numilog(service, target)
    assert calls == []
