"""Only closed synthetic Maintenance GTK milestones can enter public E2E."""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ci_e2e_maint_export", ROOT / "scripts/ci-export-e2e-summary.py",
)
assert SPEC is not None and SPEC.loader is not None
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_failed_maintenance_exposes_only_literal_checkpoint(tmp_path):
    scenario = "maintenance-tools-1366"
    source = tmp_path / "summary.json"
    source.write_text(json.dumps([
        {"scenario": scenario, "returncode": 1, "error": "PRIVATE_URL"}
    ]))
    report = tmp_path / scenario / scenario / "screen.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "checks": {
            "real_tools_route_and_action": True,
            "local_inspection_provider_filter_does_not_attribute_isbn_files": True,
            "inspection_candidates_initially_visible": True,
            "inspection_candidate_navigation_reached": "PRIVATE_untrusted_string",
        },
        "error": "PRIVATE_BEARER_TOKEN", "traceback": "/home/PRIVATE",
    }))
    row = exporter.safe_summary(source)["results"][0]
    assert row["maintenance_last_safe_checkpoint"] == "candidate_first"
    assert "PRIVATE" not in repr(row)
    report.write_text(json.dumps({"checks": {
        "inspection_candidate_navigation_reached": True,
        "local_inspection_right_candidate_side_by_side_no_attestation": True,
    }}))
    row = exporter.safe_summary(source)["results"][0]
    assert row["maintenance_last_safe_checkpoint"] == "candidate_validated"
    assert "PRIVATE" not in repr(row)
    report.write_text(json.dumps({"checks": {
        "local_inspection_manual_unlinked_filter_same_identity": True,
        "local_inspection_manual_does_not_change_candidate_set": "PRIVATE_NOT_BOOLEAN",
    }}))
    row = exporter.safe_summary(source)["results"][0]
    assert row["maintenance_last_safe_checkpoint"] == "manual_filtered"
    assert "PRIVATE" not in repr(row)
    report.write_text(json.dumps({"checks": {
        "local_inspection_manual_does_not_change_candidate_set": True,
    }}))
    row = exporter.safe_summary(source)["results"][0]
    assert row["maintenance_last_safe_checkpoint"] == "manual_restored"


def test_maintenance_checkpoint_missing_symlink_or_other_scenario_is_safe(tmp_path):
    scenario = "maintenance-tools-1280"
    source = tmp_path / "summary.json"
    source.write_text(json.dumps([{"scenario": scenario, "returncode": 1}]))
    row = exporter.safe_summary(source)["results"][0]
    assert "maintenance_last_safe_checkpoint" not in row
    report = tmp_path / scenario / scenario / "screen.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"checks": {"SECRET_PATH": True}}))
    row = exporter.safe_summary(source)["results"][0]
    assert row["maintenance_last_safe_checkpoint"] == "before_maintenance_route"
    report.unlink()
    report.symlink_to(tmp_path / "summary.json")
    row = exporter.safe_summary(source)["results"][0]
    assert "maintenance_last_safe_checkpoint" not in row
    source.write_text(json.dumps([{"scenario": "startup", "returncode": 1}]))
    row = exporter.safe_summary(source)["results"][0]
    assert "maintenance_last_safe_checkpoint" not in row



def test_all_native_maintenance_viewports_export_one_closed_checkpoint(tmp_path):
    """920/1024 failures must be diagnosable without private GTK stderr."""
    for viewport in (920, 1024, 1280, 1366, 1600):
        scenario = f"maintenance-tools-{viewport}"
        source = tmp_path / "summary.json"
        source.write_text(json.dumps([
            {"scenario": scenario, "returncode": 1, "error": "SECRET_ACCOUNT"}
        ]))
        report = tmp_path / scenario / scenario / "screen.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "checks": {
                "real_tools_route_and_action": True,
                "P1_1498-duplicates_cancelled_terminal_on_own_line": True,
                "inspection_fixture_created": "SECRET_not_boolean",
            },
            "failure_stack": "/home/SECRET_PATH token=SECRET",
            "error": "SECRET_ACCOUNT",
        }))
        output = exporter.safe_summary(source)["results"][0]
        assert output["maintenance_last_safe_checkpoint"] == "maintenance_p1_done"
        assert "SECRET" not in repr(output)
        assert "failure_stack" not in repr(output)
        report.write_text(json.dumps({
            "checks": {"real_tools_route_and_action": True}
        }))
        output = exporter.safe_summary(source)["results"][0]
        assert output["maintenance_last_safe_checkpoint"] == "maintenance_route"
