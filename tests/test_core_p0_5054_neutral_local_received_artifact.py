"""P0 #5054: filesystem evidence is never a delivery, entitlement or GET grant."""
from pathlib import Path

import pytest

from cifereca.core.local_received_artifact import received_book_file_within_root


def test_received_book_file_accepts_only_exact_nested_regular_book(tmp_path):
    root = tmp_path / "identity" / "source"
    nested = root / "acsm-final"
    nested.mkdir(parents=True)
    book = nested / "book.EPUB"
    book.write_bytes(b"synthetic book")
    assert received_book_file_within_root(book, root)
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


def test_root_grandparent_symlink_is_not_a_trusted_identity_tree(tmp_path):
    physical = tmp_path / "physical" / "alice" / "source"
    physical.mkdir(parents=True)
    book = physical / "book.epub"
    book.write_bytes(b"synthetic")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "physical", target_is_directory=True)
    aliased_root = alias / "alice" / "source"
    assert not aliased_root.is_symlink()
    assert not aliased_root.parent.is_symlink()
    assert not received_book_file_within_root(aliased_root / "book.epub", aliased_root)
    assert received_book_file_within_root(book, physical)


def test_pre_get_root_guard_rejects_alias_at_any_ancestor(tmp_path):
    from cifereca.core.local_received_artifact import trusted_local_directory

    physical = tmp_path / "physical" / "alice" / "numilog"
    physical.mkdir(parents=True)
    assert trusted_local_directory(physical)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "physical", target_is_directory=True)
    alias_root = alias / "alice" / "numilog"
    assert alias_root.is_dir()
    assert not alias_root.is_symlink() and not alias_root.parent.is_symlink()
    assert not trusted_local_directory(alias_root)
    assert not trusted_local_directory(alias)
    assert not trusted_local_directory(tmp_path / "missing")
    assert not trusted_local_directory(str(physical))
