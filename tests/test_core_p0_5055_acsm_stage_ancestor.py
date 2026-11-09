"""P0 #5055: exact private staging path never accepts symlinked ancestors."""
from pathlib import Path

from cifereca.core.loan_acsm_token_validation import (
    discard_untrusted_staged_acsm,
    staged_loan_acsm_path_safe,
)


def test_direct_acsm_in_physical_staging_is_admitted_and_cleaned(tmp_path):
    stage = tmp_path / "alice" / "bnf" / "working"
    stage.mkdir(parents=True)
    candidate = stage / "token.acsm"
    candidate.write_bytes(b"synthetic-only")
    assert staged_loan_acsm_path_safe(candidate, stage)
    discard_untrusted_staged_acsm(candidate, stage)
    assert not candidate.exists()


def test_symlinked_grandparent_never_admits_or_unlinks_foreign_token(tmp_path):
    real = tmp_path / "physical" / "alice" / "bnf" / "working"
    real.mkdir(parents=True)
    original = real / "token.acsm"
    original.write_bytes(b"private")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "physical", target_is_directory=True)
    staged = alias / "alice" / "bnf" / "working"
    candidate = staged / "token.acsm"
    assert not staged.is_symlink() and not staged.parent.is_symlink()
    assert candidate.resolve() == original.resolve()
    assert not staged_loan_acsm_path_safe(candidate, staged)
    discard_untrusted_staged_acsm(candidate, staged)
    assert original.read_bytes() == b"private"


def test_foreign_alias_and_outside_token_are_not_deleted(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    foreign = tmp_path / "foreign.acsm"
    foreign.write_bytes(b"private")
    aliased = stage / "foreign.acsm"
    aliased.symlink_to(foreign)
    assert not staged_loan_acsm_path_safe(aliased, stage)
    discard_untrusted_staged_acsm(aliased, stage)
    assert foreign.read_bytes() == b"private"
    assert aliased.is_symlink()
    assert not staged_loan_acsm_path_safe(foreign, stage)
    discard_untrusted_staged_acsm(foreign, stage)
    assert foreign.read_bytes() == b"private"


def test_non_acsm_and_incorrect_types_fail_closed(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    candidate = stage / "book.epub"
    candidate.write_bytes(b"synthetic")
    assert not staged_loan_acsm_path_safe(candidate, stage)
    assert not staged_loan_acsm_path_safe(str(candidate), stage)
    assert not staged_loan_acsm_path_safe(candidate, str(stage))
    discard_untrusted_staged_acsm(candidate, stage)
    assert candidate.read_bytes() == b"synthetic"
