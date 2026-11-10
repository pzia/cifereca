"""#4289: public GTK diagnostic may contain only reviewed callback names.

The raw probe report and screenshot are private. This gate never publishes
assertion text, tokens, account IDs, filesystem paths or DOM content.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "scripts/ci-export-e2e-summary.py"
PROBE = ROOT / "e2e/book_detail_probe.py"


def exporter():
    spec = importlib.util.spec_from_file_location("cifereca_e2e_4289", EXPORT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_case(directory, scenario, *, returncode=1, stage="expanded_search"):
    summary = directory / "summary.json"
    summary.write_text(
        json.dumps([{"scenario": scenario, "returncode": returncode,
                     "assertion_error": "Bearer SECRET"}]),
        encoding="utf-8",
    )
    private = directory / scenario / scenario / "screen.json"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text(json.dumps({
        "public_stage": stage,
        "status": "FAIL",
        "error": "Bearer SECRET https://private.invalid/?token=SECRET",
        "title": "PRIVATE TITLE",
        "path": "/home/PRIVATE",
    }), encoding="utf-8")
    return summary, private


@pytest.mark.parametrize("scenario", [
    "book-detail-1300", "book-detail-1366", "book-detail-1600",
])
def test_only_closed_stage_is_published_for_failed_book_detail(scenario):
    mod = exporter()
    with TemporaryDirectory() as tmp:
        source, private = write_case(Path(tmp), scenario)
        result = mod.safe_summary(source)
        assert result == {
            "schema": "cifereca-e2e-public-verdict/v1",
            "results": [{
                "scenario": scenario,
                "status": "FAIL",
                "returncode": 1,
                "book_detail_last_stage": "expanded_search",
            }],
        }
        published = repr(result)
        for secret in ("SECRET", "PRIVATE", "Bearer", "private.invalid", "/home"):
            assert secret not in published


def test_success_does_not_publish_private_report_even_with_stage():
    mod = exporter()
    with TemporaryDirectory() as tmp:
        source, _ = write_case(Path(tmp), "book-detail-1366", returncode=0)
        published = mod.safe_summary(source)["results"][0]
        assert published == {
            "scenario": "book-detail-1366", "status": "PASS", "returncode": 0,
        }


@pytest.mark.parametrize("stage", [
    "Bearer SECRET", "/home/PRIVATE", True, 17, None, "",
    "expanded_search\nPRIVATE", "expanded_search_private",
])
def test_unrecognized_or_untyped_stage_is_silently_discarded(stage):
    mod = exporter()
    with TemporaryDirectory() as tmp:
        source, private = write_case(Path(tmp), "book-detail-1600", stage=stage)
        result = mod.safe_summary(source)["results"][0]
        assert "book_detail_last_stage" not in result
        assert repr(stage) not in repr(result)


def test_non_book_scenarios_cannot_inject_stage_field():
    mod = exporter()
    with TemporaryDirectory() as tmp:
        source, _ = write_case(Path(tmp), "startup", stage="expanded_search")
        result = mod.safe_summary(source)["results"][0]
        assert "book_detail_last_stage" not in result


def test_private_report_symlink_is_not_followed():
    mod = exporter()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source, report = write_case(root, "book-detail-1300")
        separate = root / "private.txt"
        separate.write_text('{"public_stage": "expanded_search"}')
        report.unlink()
        report.symlink_to(separate)
        result = mod.safe_summary(source)["results"][0]
        assert "book_detail_last_stage" not in result


def test_probe_and_exporter_use_only_reviewed_function_names():
    mod = exporter()
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"activation", "callback_other"} <= mod._BOOK_DETAIL_STAGES
    assert mod._BOOK_DETAIL_STAGES - {"activation", "callback_other"} <= defined
    assert "expanded_search" in mod._BOOK_DETAIL_STAGES
    # Both the reporter and exporter must have a finite, literal-reviewed
    # vocabulary. A dynamic provider label must never enter either one.
    assignments = [node for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "_PUBLIC_STAGE_CODES"
                           for t in node.targets)]
    assert len(assignments) == 1
    expression = assignments[0].value
    assert isinstance(expression, ast.Call)
    assert isinstance(expression.func, ast.Name)
    assert expression.func.id == "frozenset"
    assert len(expression.args) == 1
    parsed = ast.literal_eval(expression.args[0])
    assert type(parsed) is list
    assert set(parsed) == mod._BOOK_DETAIL_STAGES
    assert len(parsed) <= 64


def test_anonymous_callbacks_are_explicitly_pinned_to_reviewed_stages():
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    mod = exporter()
    seen = set()
    for call in ast.walk(tree):
        if (not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or call.func.id != "later"
                or len(call.args) < 2
                or not isinstance(call.args[1], ast.Lambda)):
            continue
        callback = call.args[1].body
        assert isinstance(callback, ast.Call)
        assert isinstance(callback.func, ast.Name)
        label = [kw.value.value for kw in call.keywords
                 if kw.arg == "public_stage"
                 and isinstance(kw.value, ast.Constant)
                 and type(kw.value.value) is str]
        assert label == [callback.func.id]
        assert label[0] in mod._BOOK_DETAIL_STAGES
        seen.add(label[0])
    assert seen == {
        "verify_tab_order", "click_copy", "click_isbn",
        "component_expand",
    }


def test_owned_gtk_fixture_persists_exact_borrower_binding_before_entitlement():
    """Regression #4289: a connected session alone cannot attest a purchase."""
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "begin_owned_book_action"
    )
    calls = [
        (node.lineno, node) for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {
            "provider_connection_put", "provider_account_put", "entitlement_put",
        }
    ]
    order = [node.func.attr for _line, node in sorted(calls)]
    assert order == [
        "provider_connection_put", "provider_account_put", "entitlement_put",
    ]
    account = next(
        node for line, node in calls
        if node.func.attr == "provider_account_put"
    )
    assert len(account.args) == 1
    assert isinstance(account.args[0], ast.Dict)
    keys = [value.value for value in account.args[0].keys]
            # noqa: E128
    values = {key: value for key, value in zip(keys, account.args[0].values)}
    for mandatory in (
        "connection_id", "identity_id", "provider_id",
        "credential_id", "account_id",
    ):
        assert mandatory in values
    assert isinstance(values["connection_id"], ast.Name)
    assert values["connection_id"].id == "connection_id"
    assert isinstance(values["account_id"], ast.Attribute)
    assert values["account_id"].attr == "unique_id"
    assert isinstance(values["credential_id"], ast.Attribute)
    assert values["credential_id"].attr == "credential_id"
