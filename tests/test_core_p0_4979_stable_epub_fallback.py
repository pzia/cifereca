"""A changing EPUB/Placement cannot certify a definitive cover failure."""
import os

import pytest

from cifereca.core import epub_cover_failure_qualification as quality
from tests.test_core_p1_6168_epub_failure_qualification import attested


@pytest.mark.parametrize("change", ["grow", "replace", "unlink"])
@pytest.mark.parametrize("parser_error", [True, False])
def test_change_after_sha_before_member_verdict_is_unknown(attested, monkeypatch,
                                                         change, parser_error):
    book, resource, image = attested
    image.unlink()
    original = quality._read

    def read(*args):
        value = original(*args)
        if change == "grow":
            with book.open("ab") as stream:
                stream.write(b"changed")
        elif change == "replace":
            other = book.with_name("other.epub")
            other.write_bytes(book.read_bytes())
            other.replace(book)
        else:
            book.unlink()
        if parser_error:
            raise ValueError("ambiguous_or_missing_entry")
        # Without the post-read guard this would certify member_unusable.
        return b"not an image"

    monkeypatch.setattr(quality, "_read", read)
    result = quality.qualify_failed_epub_cover(resource, source_path=book)
    assert result.state == "retryable"
    assert result.code == "source_unverified"
    assert not result.permits_url_fallback


@pytest.mark.skipif(not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"),
                    reason="requires POSIX nonblocking FIFO")
def test_fifo_is_refused_without_waiting_for_a_writer(attested, monkeypatch):
    book, resource, image = attested
    image.unlink()
    book.unlink()
    os.mkfifo(book)
    real_open = os.open

    def guarded_open(path, flags, *args, **kwargs):
        assert flags & os.O_NONBLOCK
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(quality.os, "open", guarded_open)
    result = quality.qualify_failed_epub_cover(resource, source_path=book)
    assert result.state == "retryable"
    assert not result.permits_url_fallback
