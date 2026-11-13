"""Public provider provenance is explicit, exact, passive and non-personal."""
import json
import logging
from types import SimpleNamespace

import pytest

from cifereca.brokers import DiagnosticsBrokerImpl
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_source_sync import log_message
from cifereca.core.public_provider_diagnostics import public_connection_log_message
from cifereca.diagnostics import Diagnostics


@pytest.fixture
def application_log(tmp_path):
    path = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    previous = logger.level
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield path
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def test_broker_provider_filter_reads_only_physical_events(application_log, monkeypatch):
    # The same event must never be counted once in RAM and again on disk.
    records = []
    providers = ("paris", "kobo", "numilog", "paris.catalog")
    for index, provider in enumerate(providers):
        for offset, category in enumerate(("CONNECTION", "SYNC")):
            records.append(
                f"2026-10-25 10:00:{index * 2 + offset:02d},000 ERROR cifereca "
                "DIAG_JSON " + json.dumps({
                    "schema_version": 1, "category": category,
                    "operation": "provider_connect" if offset == 0 else "source_sync",
                    "status": "error", "message": "source failed",
                    "detail": {"provider_id": provider, "code": "failed"},
                })
            )
    records.append(
        "2026-10-25 10:00:10,000 ERROR cifereca DIAG_JSON "
        + json.dumps({
            "schema_version": 1, "category": "PLUGIN",
            "operation": "unrelated", "status": "error",
            "message": "paris mentioned without typed provider ID",
            "detail": {"email": "PRIVATE@example.invalid"},
        })
    )
    application_log.write_text("\n".join(records) + "\n", encoding="utf-8")
    monkeypatch.setattr("cifereca.paths.log_file", lambda: application_log)

    class ForbiddenMemory:
        def snapshot(self):
            raise AssertionError("physical broker must not read memory")

    broker = DiagnosticsBrokerImpl(SimpleNamespace(diagnostics=ForbiddenMemory()))
    full = broker.events(source="all", provider_id="paris", severity="error", limit=100)
    assert full["total"] == 2
    assert {row["source"] for row in full["events"]} == {"journal"}
    assert {row["module"] for row in full["events"]} == {"CONNECTION", "SYNC"}
    assert all(row["detail"]["provider_id"] == "paris" for row in full["events"])
    assert "PRIVATE@example.invalid" not in json.dumps(full)

    pages = [broker.events(source="core_events", provider_id="paris", limit=1, offset=i)
             for i in range(2)]
    assert all(page["total"] == 2 for page in pages)
    assert [page["events"][0]["event_id"] for page in pages] == [
        row["event_id"] for row in full["events"]
    ]
    assert pages[-1]["next_offset"] is None
    assert broker.events(source="app_log", provider_id="paris", module="SYNC")["total"] == 1
    assert broker.events(source="all", provider_id="paris.catalog")["total"] == 2
    assert broker.events(source="all", provider_id="provider")["total"] == 0
    unknown = broker.events(source="all", provider_id="provider.absent")
    assert unknown["events"] == [] and unknown["total"] == 0
    assert unknown["sources"] == full["sources"]



def test_generic_categories_and_runtime_do_not_attest_provider(tmp_path):
    diag = Diagnostics(level="trace")
    for category in ("KOBO", "NUMILOG", "SYNC", "PLUGIN"):
        diag.add(category, "source sync provider paris", "error", detail={"provider_id": "paris"})
    diag.add("KOBO", "automatic backup decision", "error", detail={
        "status": "error", "persisted_enabled": True, "connected_kobo": 1,
        "eligible_kobo": 1, "interval_minutes": 60,
    })
    diag.add("NUMILOG", "session_state", "error", detail={"connected": False, "reason": "connect_failed"})
    diag.add_core_kobo_adept({"operation": "status", "error_code": "adept_error"}, "error")
    rows = query_diagnostic_events(diag, log_path=tmp_path / "absent", source="core_events")
    assert all("provider_id" not in row for row in rows["events"])
    # A substitute snapshot cannot grant the real collector's provenance.
    class Substitute:
        def snapshot(self):
            return [{"timestamp": "2026-10-23T00:00:00Z", "category": "PLUGIN",
                     "operation": "provider connect", "status": "error",
                     "verified_public_provider_id": "paris",
                     "detail": {"provider_id": "paris", "code": "provider_connect_failed"}}]
    assert query_diagnostic_events(Substitute(), log_path=tmp_path / "absent",
                                   provider_id="paris")["total"] == 0


def test_retired_numilog_startup_log_does_not_gain_provider_provenance(tmp_path):
    diag = Diagnostics()
    legacy = dict(phase="completed", cause_phase="manifest_fetch",
                  code="manifest_failed", status="failed",
                  task_kind="source-materialize",
                  attempted=0, downloaded=0, skipped=0, failed=1)
    # A producer of the retired worker cannot reappear through generic add().
    diag.add("NUMILOG", "startup_materialization", "error", detail=legacy)
    trace = ("TRACE numilog startup_materialization "
             + json.dumps(legacy, sort_keys=True, separators=(",", ":")))
    log_path = tmp_path / "cifereca.log"
    bodies = [
        "TRACE kobo backup decision status=error enabled=True connected=1 eligible=1 interval=60",
        trace,
    ]
    log_path.write_text(
        "\n".join(f"2026-10-23 00:00:00,000 ERROR cifereca {b}" for b in bodies)
        + "\n",
        encoding="utf-8",
    )
    rows = query_diagnostic_events(diag, log_path=log_path)["events"]
    # The retired Kobo TRACE is no longer a provenance-bearing Core fact.
    assert not [e for e in rows if e.get("provider_id") == "kobo"]
    assert all(e.get("provider_id") != "numilog" for e in rows)
    assert "manifest_failed" not in json.dumps([
        e for e in rows if e.get("module") == "NUMILOG"
    ])



@pytest.mark.parametrize("provider", [None, True, 1, [], {}, "provider.PARIS", "paris ",
                                      "alice@example.invalid", "https://bibliotheques.paris.fr",
                                      "provider.account", "x" * 65])
def test_provider_filter_rejects_invalid_input_without_reading_sources(tmp_path, provider):
    class Unreadable:
        def snapshot(self):
            raise AssertionError("validation must precede reading")
    with pytest.raises(ValueError, match="provider"):
        query_diagnostic_events(Unreadable(), log_path=tmp_path / "absent", provider_id=provider)


def test_log_provenance_requires_exact_closed_core_record(tmp_path):
    connection = public_connection_log_message({"provider_id": "paris", "code": "provider_connect_failed"})
    sync = log_message(dict(schema_version=1, provider_id="paris", operation_kind="source_sync",
                            phase="completed", status="failed", code="source_sync_failed"))
    log = tmp_path / "cifereca.log"
    bodies = [connection + " PRIVATE", sync + " PRIVATE",
              'TRACE provider connection failure {"code":"provider_connect_failed","provider_id":"PRIVATE@example.invalid"}',
              "TRACE paris connect provider_id=paris site=PRIVATE.example.invalid book_id=PRIVATE"]
    log.write_text("\n".join(f"2026-10-23 00:00:00,000 ERROR cifereca {b}" for b in bodies)
                   + f"\n2026-10-23 00:00:00,000 ERROR cifereca.paris {connection}\n")
    rows = query_diagnostic_events(Diagnostics(), log_path=log)
    assert len(rows["events"]) == 5
    assert all("provider_id" not in e for e in rows["events"])
    assert "PRIVATE" not in json.dumps(rows)


def test_disabled_collector_still_exposes_attested_log_provider(application_log):
    diag = Diagnostics(enabled=False)
    diag.add_core_provider_connect_failure("paris")
    result = query_diagnostic_events(diag, log_path=application_log, provider_id="paris")
    assert result["status"] == "partial"
    assert len(result["events"]) == 1
    assert result["events"][0]["source"] == "app_log"


def test_provider_filter_preserves_colliding_event_references(tmp_path):
    from dataclasses import replace
    diag = Diagnostics()
    for provider in ("paris", "kobo", "paris"):
        diag.add_core_provider_connect_failure(provider)
    with diag._lock:
        for index, entry in enumerate(diag._items):
            diag._items[index] = replace(entry, timestamp="2026-10-23T00:00:00Z")
    full = query_diagnostic_events(diag, log_path=tmp_path / "absent", source="core_events")
    filtered = query_diagnostic_events(diag, log_path=tmp_path / "absent", source="core_events",
                                       provider_id="paris")
    assert len({r["event_id"] for r in full["events"]}) == 3
    assert filtered["events"] == [r for r in full["events"] if r["provider_id"] == "paris"]


def test_shared_runtime_and_plugin_log_do_not_attest_provider(application_log):
    diag = Diagnostics()
    diag.add_core_kobo_adept({"operation": "status", "error_code": "adept_error"}, "error")
    with application_log.open("a") as handle:
        handle.write("2026-10-23 00:00:00,000 ERROR cifereca_numilog "
                     "TRACE numilog session connected=False reason=connect_failed\n")
    rows = query_diagnostic_events(diag, log_path=application_log)["events"]
    # The retired Numilog-specific logger is not projected into Core events.
    # The attested ADEPT producer still appears on both canonical channels.
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"core_events", "app_log"}
    assert all("provider_id" not in r for r in rows)
    assert all(r.get("business_module", "ADEPT") == "ADEPT" for r in rows)
    assert any(r.get("business_module") == "ADEPT"
               for r in rows if r["source"] == "app_log")
    assert all(r["module"] == "ADEPT"
               for r in rows if r["source"] == "core_events")


def test_actual_registered_provider_ids_match_fixed_diagnostic_producers():
    """Import real provider descriptors in isolation; never open a session."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    paths = [root / "src", *(root / "src/plugins" / ("provider-" + p)
                               for p in ("paris", "kobo", "numilog"))]
    script = '''
import json
from pathlib import Path
from cifereca.providers import ProviderRegistry
from cifereca.core.public_provider_diagnostics import registered_public_provider_id
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics
from cifereca_paris import PLUGIN as paris
from cifereca_kobo import PLUGIN as kobo
from cifereca_numilog import PLUGIN as numilog
registry = ProviderRegistry([paris, kobo, numilog])
assert [p.descriptor.id for p in (paris, kobo, numilog)] == ["paris", "kobo", "numilog"]
for plugin in (paris, kobo, numilog):
    assert registered_public_provider_id(registry, plugin.descriptor.id) == plugin.descriptor.id
diag = Diagnostics()
diag.add_core_source_sync(kobo.descriptor.id,
    phase="completed", status="failed", code="source_sync_failed")
rows = query_diagnostic_events(diag, log_path=Path("unused"), source="core_events")["events"]
assert {r["provider_id"] for r in rows} == {kobo.descriptor.id}
assert all(r.get("detail", {}).get("operation_kind") == "source_sync" for r in rows)
for plugin in (kobo, numilog):
    expected = 1 if plugin is kobo else 0
    assert query_diagnostic_events(diag, log_path=Path("unused"), source="core_events",
        provider_id=registered_public_provider_id(registry, plugin.descriptor.id))["total"] == expected
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths))
           + os.pathsep + os.environ.get("PYTHONPATH", "")}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                             text=True, timeout=15)
    assert result.returncode == 0, result.stderr
