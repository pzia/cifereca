"""Core P0 #1120: read-only ADEPT format/path plans preserve collision guards."""
from __future__ import annotations

import pytest

from cifereca.core.adept_acsm_plan import (
    plan_produced_format_path,
    plan_publish_format_path,
)


def _never():
    pytest.fail("no random suffix should be allocated")


@pytest.mark.parametrize("actual_suffix", [".pdf", ".epub"])
def test_produced_matching_suffix_does_nothing(tmp_path, actual_suffix):
    file = tmp_path / ("book" + actual_suffix)
    file.write_bytes(b"do not touch")
    assert plan_produced_format_path(
        file, actual_suffix, unique_suffix=_never,
    ) == file
    assert file.read_bytes() == b"do not touch"


@pytest.mark.parametrize(
    ("source", "actual", "expected"),
    [
        ("book.epub", ".pdf", "book.pdf"),
        ("book.pdf", ".epub", "book.epub"),
        ("book.PDF", ".pdf", "book.PDF"),
        ("book.EPUB", ".epub", "book.EPUB"),
    ],
)
def test_produced_path_correction_uses_actual_format_without_writes(
    tmp_path, source, actual, expected,
):
    produced = tmp_path / source
    produced.write_bytes(b"staged")
    corrected = plan_produced_format_path(
        produced, actual, unique_suffix=_never,
    )
    assert corrected == tmp_path / expected
    assert produced.read_bytes() == b"staged"
    if corrected != produced:
        assert not corrected.exists()


def test_produced_correction_collision_does_not_replace_existing_file(tmp_path):
    staged = tmp_path / "book.epub"
    staged.write_bytes(b"staged")
    existing = tmp_path / "book.pdf"
    existing.write_bytes(b"already there")
    tokens = []
    def nonce():
        tokens.append(True)
        return "case-1"
    corrected = plan_produced_format_path(
        staged, ".pdf", unique_suffix=nonce,
    )
    assert corrected.name == "book.reacquired-case-1.pdf"
    assert tokens == [True]
    assert existing.read_bytes() == b"already there"
    assert staged.read_bytes() == b"staged"
    assert not corrected.exists()


@pytest.mark.parametrize("repair_mode", [False, True])
def test_publish_already_matching_suffix_ignores_collision(tmp_path, repair_mode):
    existing = tmp_path / "book.epub"
    existing.write_bytes(b"existing")
    assert plan_publish_format_path(
        existing, ".epub", repair_mode=repair_mode, unique_suffix=_never,
    ) == existing
    assert existing.read_bytes() == b"existing"


@pytest.mark.parametrize("repair_mode", [False, True])
def test_publish_corrected_name_without_collision_keeps_exact_name(tmp_path, repair_mode):
    original = tmp_path / "book.epub"
    original.write_bytes(b"staging elsewhere")
    published = plan_publish_format_path(
        original, ".pdf", repair_mode=repair_mode, unique_suffix=_never,
    )
    assert published == tmp_path / "book.pdf"
    assert original.read_bytes() == b"staging elsewhere"
    assert not published.exists()


def test_publish_collision_allocates_distinct_name_outside_repair(tmp_path):
    published = tmp_path / "borrow.epub"
    published.write_bytes(b"other file")
    corrected = tmp_path / "borrow.pdf"
    corrected.write_bytes(b"protected old file")
    candidate = plan_publish_format_path(
        published, ".pdf", repair_mode=False, unique_suffix=lambda: "token",
    )
    assert candidate == tmp_path / "borrow.reacquired-token.pdf"
    assert published.read_bytes() == b"other file"
    assert corrected.read_bytes() == b"protected old file"
    assert not candidate.exists()


def test_publish_repair_keeps_historical_destination_on_collision(tmp_path):
    original = tmp_path / "book.epub"
    original.write_bytes(b"staged name")
    corrected = tmp_path / "book.pdf"
    corrected.write_bytes(b"prior loan")
    planned = plan_publish_format_path(
        original, ".pdf", repair_mode=True, unique_suffix=_never,
    )
    assert planned == corrected
    assert corrected.read_bytes() == b"prior loan"
    assert original.read_bytes() == b"staged name"


def test_format_correction_plans_are_ordered_after_produced_rename(tmp_path):
    produced = tmp_path / "book.repairing.epub"
    produced.write_bytes(b"staged")
    destination = tmp_path / "book.epub"
    produced_corrected = plan_produced_format_path(
        produced, ".pdf", unique_suffix=_never,
    )
    # Simulate the caller's existing rename; this helper never performs it.
    produced.replace(produced_corrected)
    published_corrected = plan_publish_format_path(
        destination, ".pdf", repair_mode=True, unique_suffix=_never,
    )
    assert produced_corrected.name == "book.repairing.pdf"
    assert published_corrected.name == "book.pdf"
    assert produced_corrected.read_bytes() == b"staged"
