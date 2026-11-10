"""#5225: an ADEPT publish exception may not delete a previous valid artifact."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace as NS
import uuid

import pytest

from cifereca.core import adept_acsm_plan, adept_artifact_publication
from cifereca.providers import ProviderConfigurationError


def _publish(tmp_path, *, repair, fail_replace, monkeypatch):
    previous_bytes = b"OLD-VALID-ARTIFACT"
    new_bytes = b"NEW-VALID-ARTIFACT"
    produced = tmp_path / "new.epub"
    produced.write_bytes(new_bytes)
    destination = tmp_path / "existing.epub"
    destination.write_bytes(previous_bytes)
    old = tmp_path / "prior.epub"
    old.write_bytes(b"PRIOR-LEDGER-ARTIFACT")
    real_replace = Path.replace
    if fail_replace:
        def rejected_replace(self, target):
            if self == produced and Path(target) == destination:
                raise OSError("synthetic: rename denied")
            return real_replace(self, target)
        monkeypatch.setattr(Path, "replace", rejected_replace)
    inspection = lambda path, *_args: NS(
        valid=True, media_format="epub", protected=False,
    )
    def operation():
        return adept_artifact_publication.publish(
            result={"artifact": {"path": str(produced), "format": "epub"}},
            adept_destination=produced, acsm_format="epub",
            loan=NS(
                id="synthetic-loan", title="synthetic-title",
                record_id="synthetic-edition", source_provider="synthetic",
                source_credential="synthetic-credential",
                source_account_id="synthetic-account", identity_id="synthetic-owner",
            ),
            repair_mode=repair, destination=destination, previous_path=old,
            Path=Path, ProviderConfigurationError=ProviderConfigurationError,
            inspect_artifact=inspection, adept_acsm_plan=adept_acsm_plan,
            uuid=uuid, _TRACE_LOG=logging.getLogger("synthetic"),
        )
    return operation, produced, destination, old, new_bytes, previous_bytes


@pytest.mark.parametrize("repair", [False, True])
def test_rename_error_preserves_previous_valid_publication(
    tmp_path, monkeypatch, repair,
):
    run, produced, destination, old, new, previous = _publish(
        tmp_path, repair=repair, fail_replace=True, monkeypatch=monkeypatch,
    )
    with pytest.raises(OSError, match="rename denied"):
        run()
    assert destination.is_file() and destination.read_bytes() == previous
    assert produced.is_file() and produced.read_bytes() == new
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"


@pytest.mark.parametrize("repair", [False, True])
def test_atomic_replace_commits_new_bytes_without_unlink_first(
    tmp_path, monkeypatch, repair,
):
    run, produced, destination, old, new, previous = _publish(
        tmp_path, repair=repair, fail_replace=False, monkeypatch=monkeypatch,
    )
    final_path, *_rest = run()
    assert final_path == destination
    assert destination.read_bytes() == new
    assert not produced.exists()
    # No premature cleanup before completed index+SQLite receipt.
    assert old.exists()
    assert adept_artifact_publication.cleanup_after_commit(
        previous_path=old, final_path=destination, repair_mode=repair,
        Path=Path, loan=NS(id="synthetic-loan", identity_id="owner"),
        _TRACE_LOG=logging.getLogger("synthetic"),
    ) is repair
    assert old.exists() is (not repair)


def test_cleanup_never_removes_cross_profile_or_symlinked_prior(
    tmp_path, monkeypatch,
):
    a = tmp_path / "profile-A"
    b = tmp_path / "profile-B"
    a.mkdir()
    b.mkdir()
    previous = a / "private.epub"
    previous.write_bytes(b"OLD")
    final = b / "current.epub"
    final.write_bytes(b"NEW")
    kwargs = dict(
        previous_path=previous, final_path=final,
        repair_mode=True, Path=Path, _TRACE_LOG=logging.getLogger("synthetic"),
        loan=NS(id="synthetic", identity_id="owner"),
    )
    assert adept_artifact_publication.cleanup_after_commit(**kwargs) is False
    assert previous.read_bytes() == b"OLD"

    alias = b / "private.epub"
    alias.symlink_to(previous)
    assert adept_artifact_publication.cleanup_after_commit(
        **{**kwargs, "previous_path": alias}
    ) is False
    assert alias.is_symlink() and previous.read_bytes() == b"OLD"


def test_cleanup_failure_is_nonfatal_after_committed_publication(
    tmp_path, monkeypatch,
):
    previous = tmp_path / "old.epub"
    previous.write_bytes(b"OLD")
    final = tmp_path / "final.epub"
    final.write_bytes(b"NEW")
    real_unlink = Path.unlink
    def reject(self, *args, **kwargs):
        if self == previous:
            raise PermissionError("private OS information")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", reject)
    assert adept_artifact_publication.cleanup_after_commit(
        previous_path=previous, final_path=final, repair_mode=True,
        Path=Path, _TRACE_LOG=logging.getLogger("synthetic"),
        loan=NS(id="synthetic", identity_id="owner"),
    ) is False
    assert previous.read_bytes() == b"OLD"
    assert final.read_bytes() == b"NEW"


def test_services_invokes_cleanup_only_after_index_and_completed_ledger():
    # Protect the ordering contract against future extraction/reorganisation.
    services = (
        Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    ).read_text(encoding="utf-8")
    beginning = services.index("def _acquire_active_loan_automatically_guarded(")
    ending = services.index("\n    def ", beginning + 6)
    routine = services[beginning:ending]
    indexed = routine.index("loan_provider_delivery.index_local_loan_artifact(")
    receipt = routine.index("loan_completion_receipt.completed_receipt(")
    cleanup = routine.index("adept_artifact_publication.cleanup_after_commit(")
    assert indexed < receipt < cleanup
    assert routine.index("if acquisition.kind == \"acsm\":", receipt) < cleanup


@pytest.mark.parametrize("target", ["produced", "destination"])
def test_symlinked_adept_input_or_target_never_replaces_valid_bytes(
    tmp_path, monkeypatch, target,
):
    run, produced, destination, old, new, previous = _publish(
        tmp_path, repair=True, fail_replace=False, monkeypatch=monkeypatch,
    )
    external = tmp_path / "unrelated.epub"
    external.write_bytes(b"UNRELATED-VALID-ARTIFACT")
    selected = produced if target == "produced" else destination
    selected.unlink()
    selected.symlink_to(external)
    with pytest.raises(ProviderConfigurationError, match="Chemin d"):
        run()
    assert selected.is_symlink()
    assert external.read_bytes() == b"UNRELATED-VALID-ARTIFACT"
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"
    if target == "produced":
        assert destination.read_bytes() == previous
    else:
        assert produced.read_bytes() == new


def test_adept_publisher_rejects_symlinked_staging_directory(
    tmp_path, monkeypatch,
):
    source_dir = tmp_path / "actual-staging"
    source_dir.mkdir()
    actual = source_dir / "new.epub"
    actual.write_bytes(b"NEW")
    alias_dir = tmp_path / "stage-alias"
    alias_dir.symlink_to(source_dir, target_is_directory=True)
    alias = alias_dir / "new.epub"
    destination = tmp_path / "existing.epub"
    destination.write_bytes(b"OLD")

    with pytest.raises(ProviderConfigurationError, match="Chemin d"):
        adept_artifact_publication.publish(
            result={"artifact": {"path": str(alias), "format": "epub"}},
            adept_destination=alias, acsm_format="epub",
            loan=NS(id="synthetic", identity_id="A", source_provider="test"),
            repair_mode=False, destination=destination, previous_path=None,
            Path=Path, ProviderConfigurationError=ProviderConfigurationError,
            inspect_artifact=lambda *_a, **_k: pytest.fail("no inspection"),
            adept_acsm_plan=adept_acsm_plan, uuid=uuid,
            _TRACE_LOG=logging.getLogger("synthetic"),
        )
    assert actual.read_bytes() == b"NEW"
    assert destination.read_bytes() == b"OLD"
