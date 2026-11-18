"""#2835: generic EPUB optimization does not advertise a Kobo conversion."""
from pathlib import Path

import pytest

from cifereca_epub_optimizer import engine, profiles
from cifereca_epub_optimizer.builtin_profiles import builtin_profile


@pytest.mark.parametrize(("source", "expected"), [
    ("Manga.epub", "Manga [Clara BW].epub"),
    ("Manga.kepub.epub", "Manga [Clara BW].epub"),
    ("Manga.fxl.kepub.epub", "Manga [Clara BW].epub"),
    ("Manga.FXL.KEPUB.EPUB", "Manga [Clara BW].epub"),
    ("Manga.kepub.notes.epub", "Manga.kepub.notes [Clara BW].epub"),
    ("Manga.v2.kepub.epub", "Manga.v2 [Clara BW].epub"),
])
def test_generic_output_has_one_epub_suffix(tmp_path, source, expected):
    profile = builtin_profile("kobo-clara-bw")
    original = tmp_path / source
    original.write_bytes(b"synthetic")
    assert engine.default_output_path is profiles.default_output_path
    assert profiles.default_output_path(original, profile) == tmp_path / expected
    assert engine.default_output_path(str(original), profile) == tmp_path / expected
    assert original.read_bytes() == b"synthetic"
    assert list(tmp_path.iterdir()) == [original]
