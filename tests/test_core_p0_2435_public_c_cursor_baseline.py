"""#2435/#1023: bounded cost of the actual SQLite public-catalogue cursor.

Synthetic corpus, no provider, GTK or physical I/O claim. Timings without
trace instrumentation; SQL statement counts measured on a separate pass.
"""
from contextlib import closing
import json
import resource
from time import perf_counter_ns, process_time_ns

import pytest

from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover, _FENCE_PREFIX
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_2435_cover_cache_baseline import dist
from tests.test_core_p0_2435_preview_baseline import _process_io


@pytest.mark.parametrize("size", (16, 256))
def test_public_c_cursor_cost_live_mixed_retained(tmp_path, capsys, size):
    filename = tmp_path / "cursor.sqlite"
    report = {"documents": size, "iterations": 9, "phases": {},
              "corpus": "synthetic_sqlite", "gtk_paint_measured": False,
              "physical_io_measured": False}
    expected = {f"book-{i}" for i in range(0, size, 2)}

    def search_all(db):
        hits = []
        for index in range((size // 2 + 49) // 50):
            page = search_public_notice_c(
                db, "loups", page=index, page_size=50, now=101,
                group_by_document=True,
            )
            assert page.status == "partial"
            assert page.known_matches == page.known_documents == len(expected)
            assert page.unindexed_sources == 0
            assert page.has_more is (index + 1 < (size // 2 + 49) // 50)
            hits.extend(page.hits)
        assert len(hits) == len(expected)
        assert {hit.record_id for hit in hits} == expected
        assert all(hit.document == documents[hit.record_id] for hit in hits)
        return tuple((hit.record_id, hit.document) for hit in hits)

    def phase(db, name):
        trace = {"statements": 0, "fence_reads": 0, "mutations": 0}
        def count(statement):
            trace["statements"] += 1
            if statement.lstrip().upper().startswith("SELECT"):
                if _FENCE_PREFIX in statement:
                    trace["fence_reads"] += 1
            elif statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
                trace["mutations"] += 1
        connection = db._connect()
        connection.set_trace_callback(count)
        try:
            measured = search_all(db)
        finally:
            connection.set_trace_callback(None)
        assert trace["mutations"] == 0
        # At most the primary and exact remaining-source lookup per page,
        # independently of the number of valid live observations.
        pages = (size // 2 + 49) // 50
        assert pages <= trace["fence_reads"] <= 2 * pages
        io_before = _process_io()
        cpu = process_time_ns()
        durations = []
        for _ in range(report["iterations"]):
            started = perf_counter_ns()
            assert search_all(db) == measured
            durations.append((perf_counter_ns() - started) / 1e6)
        cpu_ms = (process_time_ns() - cpu) / 1e6
        io_after = _process_io()
        report["phases"][name] = {
            "complete_pagination_latency": dist(durations),
            "process_cpu_ms_total": round(cpu_ms, 3),
            "rss_highwater_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "sql_trace_one_pass": trace,
            "io_process_delta": {key: io_after[key] - io_before[key]
                                 for key in io_before.keys() & io_after.keys()},
        }
        return measured

    documents = {}
    with closing(Storage(filename)) as db:
        sources = []
        for i in range(size):
            record = f"book-{i}"
            source = persist(db, verified_public(
                record_id=record, title=("Les loups" if i % 2 == 0 else "Les arbres"),
                thumbnail_url=f"https://covers.example.org/{i % 8}.png",
            ), at=100)
            canonical = persist_exact_public_search_cover(db, source)
            sources.append(source)
            documents[record] = canonical.document
        live = phase(db, "live")
        for source in sources[::2]:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        assert phase(db, "mixed") == live
        for source in sources[1::2]:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(filename)) as db:
        assert phase(db, "retained_after_restart") == live
        assert db.entitlements() == [] and db.acquisitions() == []
    with capsys.disabled():
        print("PUBLIC_C_CURSOR_BASELINE " + json.dumps(report, sort_keys=True), flush=True)
