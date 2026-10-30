"""#3723: BnF ACSM stage is inode-pinned across atomic token file writes.

Synthetic payload only; no real PNB, ADEPT, rights, SQLite or Core publish.
"""
from __future__ import annotations

import os

import pytest

from bnf_token_fixture import TOKEN
import cifereca_bnf_pret_download as delivery
from cifereca_bnf_pret_http import ReaderHTTPError


@pytest.mark.parametrize("moment", ["temporary_create", "final_link"])
def test_bnf_acsm_staging_swap_never_writes_or_keeps_file_outside(
    tmp_path, monkeypatch, moment,
):
    stage = tmp_path / "private-stage"
    stage.mkdir()
    previous = tmp_path / "renamed-stage"
    outside = tmp_path / "outside"
    outside.mkdir()
    swapped = False

    def swap():
        nonlocal swapped
        if not swapped:
            stage.rename(previous)
            stage.symlink_to(outside, target_is_directory=True)
            swapped = True

    if moment == "temporary_create":
        original = os.open

        def open_with_stage_swap(name, flags, *args, **kwargs):
            if kwargs.get("dir_fd") is not None:
                swap()
            return original(name, flags, *args, **kwargs)

        monkeypatch.setattr(delivery.os, "open", open_with_stage_swap)
    else:
        original = os.link

        def link_with_stage_swap(src, dst, *args, **kwargs):
            result = original(src, dst, *args, **kwargs)
            swap()
            return result

        monkeypatch.setattr(delivery.os, "link", link_with_stage_swap)

    with pytest.raises(ReaderHTTPError, match="download_storage_failed"):
        delivery.save_private_loan_token(TOKEN, stage)
    assert swapped
    assert list(outside.iterdir()) == []
    assert list(previous.iterdir()) == []


def test_bnf_valid_acsm_still_staged_exactly_once(tmp_path):
    candidate = delivery.save_private_loan_token(TOKEN, tmp_path)
    assert candidate.parent == tmp_path
    assert candidate.suffix == ".acsm"
    assert candidate.read_bytes() == TOKEN
    assert sorted(x.name for x in tmp_path.iterdir()) == [candidate.name]
