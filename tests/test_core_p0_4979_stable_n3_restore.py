"""Cold N3 restores only from the still-named authorized EPUB Placement."""
import pytest

from cifereca.core import epub_cover_materialization as n3
from cifereca.core.epub_cover_failure_qualification import qualify_failed_epub_cover
from tests.test_core_p1_6168_epub_failure_qualification import attested


@pytest.mark.parametrize("change", ["parent_rename", "parent_replaced", "file_replaced", "symlink", "grow"])
def test_path_rebind_after_member_read_does_not_publish(attested, monkeypatch, change):
    book, resource, image = attested
    root = book.parent
    folder = root / "placement"
    folder.mkdir()
    book = book.rename(folder / book.name)
    image.unlink()
    original = n3._read

    def read(*args):
        content = original(*args)
        if change in ("parent_rename", "parent_replaced"):
            # The open regular inode (including ctime) can stay unchanged:
            # only the path-to-inode association has been revoked.
            moved = folder.rename(root / "moved")
            if change == "parent_replaced":
                folder.mkdir()
                book.write_bytes((moved / book.name).read_bytes())
        elif change == "grow":
            with book.open("ab") as stream:
                stream.write(b"changed after reading")
        else:
            other = root / "replacement.epub"
            other.write_bytes(book.read_bytes())
            if change == "file_replaced":
                other.replace(book)
            else:
                book.unlink()
                book.symlink_to(other)
        return content

    monkeypatch.setattr(n3, "_read", read)
    assert n3.EpubCoverPhysicalCache._restore(resource, book) is None
    assert not image.exists()
    # For a vanished path the existing failure qualifier must also refuse
    # to promote another source. A new identical file requires a new read.
    if change in ("parent_rename", "symlink", "grow"):
        result = qualify_failed_epub_cover(resource, source_path=book)
        assert result.state == "retryable" and not result.permits_url_fallback


def test_stable_named_placement_still_restores_exact_cached_image(attested):
    book, resource, image = attested
    image.unlink()
    uri = n3.EpubCoverPhysicalCache._restore(resource, book)
    assert uri == image.resolve().as_uri()
    assert n3.valid_cached_cover_path(image)
