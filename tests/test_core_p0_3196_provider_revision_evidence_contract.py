"""P0 #3196: an exact provider revision survives the canonical v1/v2 bridge.

A revision is a comparison input, not a right, physical file, origin receipt,
download authorization, or permission to retire a legacy backup. No I/O.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.plugin_api import (
    ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot,
    from_legacy_rights_snapshot, as_legacy_rights_snapshot,
)


def _right(**kwargs):
    row = {
        "edition_id": "edition", "right_id": "edition",
        "kind": "purchase", "state": "active",
        "evidence_kind": "provider_response",
        "evidence_ref": "authenticated-manifest",
        "observed_at": "2026-11-06T11:00:00+00:00",
    }
    row.update(kwargs)
    return ProviderRightObservation(**row)


def _snapshot(row):
    return ProviderRightsSnapshot(
        scope=ProviderRightsScope("alice", "kobo", "vault", "buyer"),
        branch="personal_manifest", rights=(row,),
    )


@pytest.mark.parametrize("revision", ["rev-one", "a" * 64, "version.2026-11-06"])
def test_exact_revision_round_trips_without_new_authority(revision):
    first = _right(provider_revision_id=revision)
    v2 = from_legacy_rights_snapshot(_snapshot(first))
    assert v2.resources[0].provider_revision_id == revision
    assert v2.resources[0].source.scope.account_id == "buyer"
    assert v2.resources[0].source.scope.credential_id == "vault"
    assert v2.resources[0].authorizes_acquisition is False
    second = as_legacy_rights_snapshot(v2)
    assert second.rights[0].provider_revision_id == revision
    assert second.rights[0].authorizes_mutation is False
    assert second.rights[0].delivery == "unknown"
    assert first == second.rights[0]


def test_revision_absence_cannot_be_inferred_from_edition_or_sha():
    first = _right()
    v2 = from_legacy_rights_snapshot(_snapshot(first))
    assert first.provider_revision_id is None
    assert v2.resources[0].provider_revision_id is None
    assert as_legacy_rights_snapshot(v2).rights[0].provider_revision_id is None


@pytest.mark.parametrize("revision", [
    "", " leading", "trailing ", "line\nfeed", "tab\tvalue",
    "bad\x00revision", "del\x7frevision", "x" * 257, 123, True, b"bytes",
])
def test_invalid_provider_revision_is_rejected_in_v1_and_v2(revision):
    with pytest.raises(ValueError, match="invalid_provider_revision_id"):
        _right(provider_revision_id=revision)
    clean = from_legacy_rights_snapshot(_snapshot(_right()))
    with pytest.raises(ValueError, match="invalid_downloadable_observation"):
        replace(clean.resources[0], provider_revision_id=revision)


def test_unattested_v1_revision_never_becomes_positive_v2_resource():
    legacy = ProviderRightObservation(
        edition_id="edition", right_id="edition",
        kind="purchase", provider_revision_id="revision-one",
    )
    converted = from_legacy_rights_snapshot(_snapshot(legacy))
    assert converted.resources == ()
    assert converted.coverage == "unknown"
