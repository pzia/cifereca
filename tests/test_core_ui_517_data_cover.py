from __future__ import annotations

import base64

import httpx

from cifereca.gui import cover_loader as cover_module


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_data_cover_is_decoded_locally_without_http(monkeypatch, tmp_path):
    monkeypatch.setattr(cover_module, "cache_dir", lambda: tmp_path)

    def forbidden_http(*_args, **_kwargs):
        raise AssertionError("data: covers must never reach HTTP")

    monkeypatch.setattr(httpx.Client, "get", forbidden_http)
    loader = cover_module.CoverLoader(max_workers=2)
    try:
        encoded = base64.b64encode(PNG).decode("ascii")
        results = []
        loader.load(f"data:image/png;base64,{encoded}", results.append)

        assert len(results) == 1
        assert results[0] is not None
        assert results[0].read_bytes() == PNG
        assert loader.stats()["data_url_ok"] == 1
        assert loader.stats()["download_ok"] == 0
        assert loader.stats()["download_failed"] == 0
    finally:
        loader.close()


def test_invalid_data_cover_is_rejected_without_http(monkeypatch, tmp_path):
    monkeypatch.setattr(cover_module, "cache_dir", lambda: tmp_path)

    def forbidden_http(*_args, **_kwargs):
        raise AssertionError("invalid data: covers must never reach HTTP")

    monkeypatch.setattr(httpx.Client, "get", forbidden_http)
    loader = cover_module.CoverLoader(max_workers=2)
    try:
        results = []
        loader.load("data:image/png;base64,not-base64!", results.append)

        assert results == [None]
        assert loader.stats()["data_url_invalid"] == 1
        assert loader.stats()["download_ok"] == 0
        assert loader.stats()["download_failed"] == 0
    finally:
        loader.close()
