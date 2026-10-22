"""Both search APIs must retain a real offline page's truncation warning."""
import httpx
import pytest

from cifereca.core import known_catalog_fallback
from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


@pytest.mark.parametrize("api", ["synchronous", "progressive"])
@pytest.mark.parametrize("count", [50, 51])
def test_real_sqlite_fallback_retains_first_page_coverage_and_good_source(
    tmp_path, monkeypatch, api, count,
):
    service = _service(tmp_path)
    try:
        for index in range(count):
            _known(service, provider="bad", key=f"known-{index:03d}",
                   title="Progressive Book")
        # Neither another identity nor private rights may contribute to the
        # source's public match count or create a false truncation warning.
        _known(service, provider="bad", identity="bob", key="foreign", title="Progressive Book")
        _known(service, provider="bad", key="owned", state="remote_owned", title="Progressive Book")
        _known(service, provider="bad", key="private", collection="library", title="Progressive Book")
        bad, good = _FakeSession("bad"), _FakeSession("good")
        requests, pages = [], []

        def unavailable(*_args, **_kwargs):
            requests.append("request")
            raise httpx.ReadTimeout("PRIVATE_REQUEST_TOKEN")

        original = known_catalog_fallback.known_public_fallback_page

        def page(*args, **kwargs):
            result = original(*args, **kwargs)
            pages.append(result)
            return result

        bad.search = unavailable
        service._sessions = {"bad": bad, "good": good}
        monkeypatch.setattr(known_catalog_fallback, "known_public_fallback_page", page)
        if api == "progressive":
            batches = []
            summary = service.search_progressive(
                "Progressive Book", field="title", force=True, on_batch=batches.append,
            )
            affected = next(batch for batch in batches if batch.provider_id == "bad")
            rows, warnings = affected.results, affected.warnings
            assert affected.result_status == "known_catalog_stale"
            assert summary.result_count == 51  # 50 stale plus the good provider
        else:
            results = service.search("Progressive Book", field="title", force=True)
            rows = [row for row in results if row.source_provider == "bad"]
            warnings = service.last_search_warnings
            assert any(row.source_provider == "good" for row in results)
        assert len(requests) == len(pages) == 1
        assert pages[0].matched_count == count
        assert len(rows) == 50
        limited = [warning for warning in warnings if "known_catalog_page_truncated" in warning]
        assert len(limited) == (1 if count > 50 else 0)
        assert all(not row.owned and not row.downloadable and row.download_url is None for row in rows)
        assert "PRIVATE" not in repr(warnings)
    finally:
        service.close()
