import pytest

from scripts.ci_agent_d_ast_progress import METRIC, summarize


def entry(path, line, *, kinds=("comparisons",), providers=("fixture-a", "fixture-b")):
    return dict(path=f"src/cifereca/{path}", line=line, function="read",
                kinds=kinds, providers=providers)


def worklist(*entries, errors=()):
    return dict(metric=METRIC, unique_lines=len(entries), entries=entries, parse_errors=errors)


def test_exclusive_group_counts_include_overlaps_once():
    report = summarize(worklist(
        entry("core/loan_adept_profile_guard.py", 1), entry("plugin_api/models.py", 1),
        entry("core/runtime.py", 1), entry("gui/detail_panel.py", 1), entry("storage.py", 1),
    ))
    assert report["unique_lines"] == 5
    assert report["groups"] == {"A": 1, "B": 1, "C": 1, "CoreUI": 1, "Transversal": 1}


def test_line_shifts_ignored_and_changed_signature_detected_at_same_count():
    base = worklist(entry("services.py", 11), entry("services.py", 12))
    current = worklist(entry("services.py", 21), entry("services.py", 22, kinds=("imports",)))
    report = summarize(current, base)
    assert report["group_deltas"]["B"] == 0
    assert report["changed_files"] == report["comparison"]["functions"] == []
    assert report["increased_signatures"] == [dict(
        path="src/cifereca/services.py", function="read", kinds=("imports",),
        providers=("fixture-a", "fixture-b"), increase=1,
    )]


def test_parse_failure_prevents_comparison_and_keeps_diagnostic():
    healthy = worklist(entry("services.py", 1))
    errors = ({"path": "src/cifereca/services.py", "error": "SyntaxError"},)
    damaged = worklist(errors=errors)
    assert summarize(damaged, healthy)["parse_errors"] == errors
    result = summarize(healthy, damaged)
    assert result["baseline_parse_errors"] == errors
    assert "comparison" not in result


def test_duplicate_physical_line_is_rejected():
    first = entry("services.py", 1)
    with pytest.raises(ValueError, match="unique-line count"):
        summarize(worklist(first, {**first, "providers": ("fixture-c",)}))
