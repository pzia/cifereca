from trace_report import build_report, parse_trace_lines


def sample_lines():
    return [
        "INFO TRACE recommendations performance books=1000 queries=8 providers=2 snapshot_ms=812.000 snapshot_cached=false profile_ms=19.000 providers_ms=1.000 catalog_ms=120.000 rank_ms=35.000 total_ms=990.000 cancelled=false",
        "INFO TRACE recommendations ui books=1000 results=120 apply_ms=12.000 total_ui_ms=1010.000",
        "INFO TRACE recommendations performance books=1000 queries=8 providers=2 snapshot_ms=0.020 snapshot_cached=true profile_ms=18.000 providers_ms=1.000 catalog_ms=115.000 rank_ms=34.000 total_ms=180.000 cancelled=false",
        "INFO TRACE recommendations ui books=1000 results=120 apply_ms=11.000 total_ui_ms=195.000",
        "INFO TRACE recommendations performance books=1000 queries=8 providers=2 snapshot_ms=0.015 snapshot_cached=true profile_ms=18.000 providers_ms=1.000 catalog_ms=116.000 rank_ms=33.000 total_ms=181.000 cancelled=false",
        "INFO TRACE recommendations ui books=1000 results=120 apply_ms=10.000 total_ui_ms=196.000",
    ]


def test_parse_trace_lines_extracts_typed_values():
    rows = parse_trace_lines(sample_lines())

    assert len(rows) == 6
    assert rows[0].kind == "performance"
    assert rows[0].values["books"] == 1000
    assert rows[0].values["snapshot_cached"] is False
    assert rows[0].values["snapshot_ms"] == 812.0


def test_build_report_summarizes_warm_cache_and_ui_timings():
    report = build_report(parse_trace_lines(sample_lines()))

    assert "refreshes=3 ui=3" in report
    assert "snapshot_cache: cached=2 uncached=1" in report
    assert "snapshot chaud/froid: 812.000 ms → 0.018 ms" in report
    assert "total UI:" in report
    assert "OK: après l'amorçage" in report


def test_report_flags_repeated_uncached_snapshots():
    lines = [
        f"TRACE recommendations performance books=1000 queries=8 providers=1 snapshot_ms={value} snapshot_cached=false profile_ms=1 providers_ms=1 catalog_ms=1 rank_ms=1 total_ms=10 cancelled=false"
        for value in (800, 700, 750)
    ]

    report = build_report(parse_trace_lines(lines))

    assert "ATTENTION: plusieurs snapshots non cachés" in report


def test_parser_ignores_unrelated_log_lines():
    assert parse_trace_lines(["INFO startup ok", "TRACE autre chose"]) == []
