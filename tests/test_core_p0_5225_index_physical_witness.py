"""#5225: reject changes across automatic loan inspection and index callbacks.

Physical witness only: an already-committed legacy index is not rolled back.
"""
import logging
import os
import time
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_provider_delivery import index_local_loan_artifact
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p1_5225_loan_index_symlink_preflight import fake_loan


@pytest.mark.parametrize("phase", ["inspection", "index"])
@pytest.mark.parametrize("change", ["replacement", "restored_mtime", "hardlink", "parent", "fifo"])
def test_changed_output_cannot_attest_completion(tmp_path, phase, change):
    parent = tmp_path / "library"
    parent.mkdir()
    path = parent / "book.pdf"
    path.write_bytes(b"original")
    before = path.stat()
    calls = []

    def mutate():
        if change == "replacement":
            path.rename(parent / "old.pdf")
            path.write_bytes(b"original")
        elif change == "restored_mtime":
            path.write_bytes(b"modified")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert path.stat().st_ctime_ns != before.st_ctime_ns
        elif change == "hardlink":
            os.link(path, parent / "alias.pdf")
        elif change == "parent":
            parent.rename(tmp_path / "old-library")
            parent.mkdir()
            os.link(tmp_path / "old-library" / path.name, path)
        else:
            path.rename(parent / "old.pdf")
            os.mkfifo(path)

    def inspect(received):
        assert received == path
        calls.append("inspection")
        if phase == "inspection":
            mutate()
        return NS(valid=True, media_format="pdf", protected=False)

    def index(received, **kwargs):
        assert received == path
        calls.append("index")
        if phase == "index":
            mutate()
        return {"status": "indexed"}

    with pytest.raises(ProviderConfigurationError, match="Artefact local invalide"):
        index_local_loan_artifact(
            NS(catalog_index_path=index), fake_loan(), NS(identity_id="alice"), path,
            time=time, _TRACE_LOG=logging.getLogger("test.loan.witness"),
            inspect_artifact=inspect, ProviderConfigurationError=ProviderConfigurationError,
        )
    assert calls == (["inspection"] if phase == "inspection" else ["inspection", "index"])
    if change == "fifo":
        assert path.is_fifo() and (parent / "old.pdf").read_bytes() == b"original"
    elif change == "replacement":
        assert (parent / "old.pdf").read_bytes() == path.read_bytes() == b"original"
    elif change == "hardlink":
        assert (parent / "alias.pdf").samefile(path)
    elif change == "parent":
        assert (tmp_path / "old-library" / path.name).samefile(path)
    else:
        assert path.read_bytes() == b"modified"


def test_fifo_at_open_is_nonblocking_and_never_inspected(tmp_path, monkeypatch):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"original")
    real_open = os.open
    swapped = []

    def substitute(name, flags, *args, **kwargs):
        if name == path.name and kwargs.get("dir_fd") is not None and not swapped:
            path.rename(tmp_path / "old.pdf")
            os.mkfifo(path)
            swapped.append(True)
            assert flags & os.O_NONBLOCK
        return real_open(name, flags, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("replaced input cannot be inspected or indexed")

    monkeypatch.setattr(os, "open", substitute)
    with pytest.raises(ProviderConfigurationError, match="Artefact local invalide"):
        index_local_loan_artifact(
            NS(catalog_index_path=forbidden), fake_loan(), NS(identity_id="alice"), path,
            time=time, _TRACE_LOG=logging.getLogger("test.loan.witness"),
            inspect_artifact=forbidden, ProviderConfigurationError=ProviderConfigurationError,
        )
    assert swapped == [True] and path.is_fifo()
    assert (tmp_path / "old.pdf").read_bytes() == b"original"
