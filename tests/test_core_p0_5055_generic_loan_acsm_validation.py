"""P0 #5055: provider-neutral loan ACSM candidate verification is passive."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_acsm_token_validation import (
    MAX_LOAN_TOKEN_BYTES, discard_untrusted_staged_acsm, valid_loan_acsm_token,
)
from cifereca.core import bnf_explicit_acsm as legacy


LOAN = (b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
        b'fulfillmentType="loan"/>')
BUY = (b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
       b'fulfillmentType="buy"/>')


def _inspector(*, valid=True, media_format="acsm"):
    return lambda _path: NS(valid=valid, media_format=media_format)


def test_bnf_adapter_reexports_exact_generic_validators():
    assert legacy._valid_loan_token is valid_loan_acsm_token
    assert legacy._discard_invalid_candidate is discard_untrusted_staged_acsm
    assert legacy._MAX_BYTES == MAX_LOAN_TOKEN_BYTES


@pytest.mark.parametrize(("content", "expected"), [
    (LOAN, True),
    (BUY, False),
    (b'<fulfillmentToken fulfillmentType="loan"/>', False),
    (b'<!DOCTYPE x><fulfillmentToken xmlns="http://ns.adobe.com/adept" '
     b'fulfillmentType="loan"/>', False),
    (b'<!ENTITY x "secret"><fulfillmentToken xmlns="http://ns.adobe.com/adept" '
     b'fulfillmentType="loan"/>', False),
    (b'', False),
    (b'X' * (MAX_LOAN_TOKEN_BYTES + 1), False),
])
def test_token_is_validated_by_structure_not_filename_or_provider(tmp_path, content, expected):
    path = tmp_path / "candidate.acsm"
    path.write_bytes(content)
    assert valid_loan_acsm_token(path, _inspector()) is expected
    assert path.exists()  # inspection never mutates token bytes


@pytest.mark.parametrize(("valid", "media"), [(False, "acsm"), (True, "epub")])
def test_invalid_artifact_inspector_never_grants_candidate(tmp_path, valid, media):
    path = tmp_path / "candidate.acsm"
    path.write_bytes(LOAN)
    assert not valid_loan_acsm_token(
        path, _inspector(valid=valid, media_format=media)
    )


def test_cleanup_is_staging_scoped_and_never_follows_symlinks(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    outside = tmp_path / "outside.acsm"
    outside.write_bytes(LOAN)
    linked = stage / "linked.acsm"
    linked.symlink_to(outside)
    direct = stage / "direct.acsm"
    direct.write_bytes(LOAN)
    discard_untrusted_staged_acsm(linked, stage)
    assert linked.is_symlink() and outside.read_bytes() == LOAN
    discard_untrusted_staged_acsm(outside, stage)
    assert outside.read_bytes() == LOAN
    discard_untrusted_staged_acsm(direct, stage)
    assert not direct.exists()
    other = stage / "not_acsm.epub"
    other.write_bytes(LOAN)
    discard_untrusted_staged_acsm(other, stage)
    assert other.read_bytes() == LOAN
