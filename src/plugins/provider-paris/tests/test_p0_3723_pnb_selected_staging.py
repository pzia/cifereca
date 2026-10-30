"""#3723 Paris PNB selected candidate remains in Core's pinned staging inode.

All HTTP is synthetic; no live borrower, ADEPT or Core transaction.
"""
from __future__ import annotations

import os

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p0_3252_paris_download_scope import prepare
from tests.test_p1_3252_paris_selected_right_freshness import raw_loan
from cifereca_paris_selected_staging import save_selected_candidate


def _deliver(session, account, offer, staging):
    return session.rights_selected_delivery_candidate(
        account, edition_id="edition-1", right_id="loan-1",
        kind="loan", branch="dilicom",
        delivery_offer_id=offer, target_dir=staging,
        explicit_user_action=True,
    )


def test_paris_selected_pnb_get_stage_swap_never_writes_outside(
    tmp_path, monkeypatch,
):
    stage = tmp_path / "stage"
    stage.mkdir()
    old_stage = tmp_path / "old-stage"
    outside = tmp_path / "outside"
    outside.mkdir()
    session, account, _, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    offer = session.rights_observation_snapshot(account).rights[0].delivery_offer_id
    real_get = client._get_pnb_response

    def replace_on_get(url):
        stage.rename(old_stage)
        stage.symlink_to(outside, target_is_directory=True)
        return real_get(url)

    client._get_pnb_response = replace_on_get
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_candidate_staging_rebound"):
            _deliver(session, account, offer, stage)
        assert len(gets) == 1
        assert list(old_stage.iterdir()) == []
        assert list(outside.iterdir()) == []
    finally:
        session.close()


def test_paris_selected_candidate_does_not_overwrite_existing_file(
    tmp_path, monkeypatch,
):
    stage = tmp_path / "stage"
    stage.mkdir()
    session, account, _, client, gets = prepare(tmp_path, monkeypatch)
    client.digital = [raw_loan()]
    offer = session.rights_observation_snapshot(account).rights[0].delivery_offer_id
    existing = stage / "Fresh synthetic loan.pdf"
    existing.write_bytes(b"PERSONAL COPY MUST SURVIVE")
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_candidate_staging_occupied"):
            _deliver(session, account, offer, stage)
        assert existing.read_bytes() == b"PERSONAL COPY MUST SURVIVE"
        assert len(gets) == 1 and list(stage.iterdir()) == [existing]
    finally:
        session.close()


def test_paris_atomic_candidate_link_swap_removes_candidate_from_old_inode(
    tmp_path, monkeypatch,
):
    import cifereca_paris_selected_staging as selected
    stage = tmp_path / "stage"
    stage.mkdir()
    old_stage = tmp_path / "old-stage"
    outside = tmp_path / "outside"
    outside.mkdir()
    stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    real_link = os.link

    def swap_after_link(src, dst, *args, **kwargs):
        result = real_link(src, dst, *args, **kwargs)
        stage.rename(old_stage)
        stage.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(selected.os, "link", swap_after_link)
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_candidate_staging_rebound"):
            save_selected_candidate(stage, stage_fd, "candidate.acsm", b"SYNTHETIC")
        assert list(outside.iterdir()) == []
        assert list(old_stage.iterdir()) == []
    finally:
        os.close(stage_fd)
