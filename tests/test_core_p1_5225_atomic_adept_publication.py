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
    assert old.exists() is (not repair)
