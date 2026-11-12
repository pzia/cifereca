"""P0 #5077: consent inventory must never disclose identifiers or mutate DB."""
import importlib.util
import json
from pathlib import Path
import sqlite3


def _load():
    script = Path(__file__).resolve().parents[1] / "tools" / "audit_source_consent_cutover.py"
    spec = importlib.util.spec_from_file_location("consent_inventory_tool", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readonly_inventory_counts_without_leaking_source_ids(tmp_path, capsys):
    module = _load()
    path = tmp_path / "consent.sqlite"
    secret = "private-identity-credential-account"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", [
            ("sync.source.numilog.materialization.consent.v1." + secret, "on_start:v1"),
            ("sync.source.numilog.materialization.epoch.v1." + secret, "a" * 32),
            ("sync.source.bnf_auto." + secret, "1"),
            ("sync.source.bnf_auto." + secret + ".revision", "b" * 32),
            ("sync.source.materialization.on_start." + secret, "1"),
            ("sync.source.materialization.on_start." + secret + ".revision", "c" * 32),
            ("sync.source.acquisition-policy.v1." + secret, "invalid:secret"),
        ])
    before = path.read_bytes()
    assert module.main([str(path)]) == 0
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "invalid:secret" not in output.out + output.err
    assert json.loads(output.out) == {
        "schema": "cifereca.private-id-gamma-908onsent-inventory/1",
        "read_only": True,
        "counts": {
            "numilog_purchase": 1, "numilog_epoch": 1,
            "bnf_loan": 1, "kobo_purchase": 1, "cas_source_policy": 1,
        },
        "legacy_modes": {
            "numilog_purchase": {"on_with_valid_revision": 1,
                                 "on_missing_or_invalid_revision": 0,
                                 "not_on": 0},
            "bnf_loan": {"on_with_valid_revision": 1,
                         "on_missing_or_invalid_revision": 0, "not_on": 0},
            "kobo_purchase": {"on_with_valid_revision": 1,
                              "on_missing_or_invalid_revision": 0,
                              "not_on": 0},
        },
    }
    assert path.read_bytes() == before


def test_missing_database_fails_closed_and_is_not_created(tmp_path, capsys):
    module = _load()
    path = tmp_path / "missing.sqlite"
    assert module.main([str(path)]) == 2
    assert not path.exists()
    assert capsys.readouterr().err.strip() == "source_consent_inventory_unavailable"


def test_legacy_on_without_revision_is_counted_but_not_promoted(tmp_path, capsys):
    module = _load()
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", [
            ("sync.source.numilog.materialization.consent.v1.private-id-alpha-906", "on_start:v1"),
            ("sync.source.numilog.materialization.epoch.v1.private-id-alpha-906", "UPPERCASE-INVALID"),
            ("sync.source.bnf_auto.private-id-beta-907", "1"),
            ("sync.source.materialization.on_start.private-id-gamma-908", "0"),
            ("sync.source.materialization.on_start.private-id-delta-909", "1"),
        ])
    assert module.main([str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["legacy_modes"] == {
        "numilog_purchase": {"on_with_valid_revision": 0,
                             "on_missing_or_invalid_revision": 1, "not_on": 0},
        "bnf_loan": {"on_with_valid_revision": 0,
                     "on_missing_or_invalid_revision": 1, "not_on": 0},
        "kobo_purchase": {"on_with_valid_revision": 0,
                          "on_missing_or_invalid_revision": 1, "not_on": 1},
    }
    assert "private-id-alpha-906" not in json.dumps(result)
    assert "private-id-beta-907" not in json.dumps(result)
    assert "private-id-gamma-908" not in json.dumps(result)
    assert "private-id-delta-909" not in json.dumps(result)
