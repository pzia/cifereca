"""P0 #5054: provider-neutral filesystem evidence is not a delivery/right."""
from pathlib import Path

import pytest

from cifereca.core.local_received_artifact import received_book_file_within_root
from cifereca.core.numilog_manual_acquisition import require_local_artifact
from cifereca.plugin_api import ProviderConfigurationError


def test_received_book_file_accepts_only_exact_nested_regular_book(tmp_path):
    root = tmp_path / "identity" / "source"
    nested = root / "acsm-final"
    nested.mkdir(parents=True)
    book = nested / "book.EPUB"
    book.write_bytes(b"synthetic book")
    assert received_book_file_within_root(book, root)
    assert require_local_artifact(book, root) is None
    assert not received_book_file_within_root(book.with_suffix(".acsm"), root)


@pytest.mark.parametrize("kind", ["foreign", "sibling", "alias", "file_alias", "bad_suffix", "missing"])
def test_received_book_file_refuses_cross_scope_and_aliases(tmp_path, kind):
    root = tmp_path / "alice" / "source"
    root.mkdir(parents=True)
    nested = root / "safe"
    nested.mkdir()
    book = nested / "original.epub"
    book.write_bytes(b"safe")
    if kind == "foreign":
        other = tmp_path / "bob"
        other.mkdir()
        candidate = other / "book.epub"
        candidate.write_bytes(b"safe")
    elif kind == "sibling":
        other = tmp_path / "alice" / "source-foreign"
        other.mkdir()
        candidate = other / "book.epub"
        candidate.write_bytes(b"safe")
    elif kind == "alias":
        other = tmp_path / "outside"
        other.mkdir()
        (other / "book.epub").write_bytes(b"safe")
        alias = root / "indirect"
        alias.symlink_to(other, target_is_directory=True)
        candidate = alias / "book.epub"
    elif kind == "file_alias":
        candidate = nested / "alias.epub"
        candidate.symlink_to(book)
    elif kind == "bad_suffix":
        candidate = nested / "token.acsm"
        candidate.write_bytes(b"not a book")
    else:
        candidate = nested / "missing.epub"
    assert not received_book_file_within_root(candidate, root)
    with pytest.raises(ProviderConfigurationError, match="artefact local non attesté"):
        require_local_artifact(candidate, root)


def test_invalid_root_or_untrusted_argument_fails_closed(tmp_path):
    target = tmp_path / "books"
    target.mkdir()
    file = target / "book.pdf"
    file.write_bytes(b"synthetic")
    assert not received_book_file_within_root(str(file), target)
    assert not received_book_file_within_root(file, str(target))
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    assert not received_book_file_within_root(alias / "book.pdf", alias)
    assert not received_book_file_within_root(file, alias)
