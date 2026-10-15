"""#1494: robust, private, read-only language audit with untrusted catalogue data.

Only synthetic brokers and an instrumented public normalizer. No GTK, network,
file writes or mutation of catalogue language fields.
"""
from __future__ import annotations

import json

import pytest

import cifereca_library_audits as audits
from cifereca.plugin_api import MaintenanceContext


class StringBomb:
    def __str__(self):
        raise AssertionError("non-text value must never be stringified")


class Context:
    def __init__(self, rows):
        self.rows = rows
        self.requested = []

    def broker(self, name):
        assert name == "catalog"
        return self

    def catalog_entries(self, **options):
        self.requested.append(options)
        return self.rows


class Control:
    def __init__(self):
        self.events = []
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **kwargs):
        self.events.append(kwargs)


def test_malformed_tags_and_failing_normalizers_are_isolated_and_redacted(monkeypatch):
    bomb = StringBomb()
    rows = [
        {
            "title": "PRIVATE_BOOK_ONE",
            "languages": ["FR", bomb, False, {"key": "PRIVATE_SECRET"}, "en", "", 3],
            "metadata": {"language": "FR", "langs": ("de", None)},
        },
        {
            "title": "PRIVATE_BOOK_TWO",
            "languages": ["bad", "en-US", "xx"],
            "metadata": {"language": 42},
        },
    ]
    original = [list(rows[0]["languages"]), dict(rows[1])]
    calls = []

    def normalizer(raw):
        calls.append(raw)
        if raw == "bad":
            PrivateError = type("Bearer_PRIVATE_ACCESS_TOKEN", (ValueError,), {})
            raise PrivateError("/home/PRIVATE_OWNER/secret.epub")
        if raw == "xx":
            return {"unexpected": "PRIVATE_OBJECT"}
        return {"FR": "fr"}.get(raw, raw)

    monkeypatch.setattr(audits, "normalize_language_tag", normalizer)
    ctx = Context(rows)
    tool = audits.LanguageAuditTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="account"))
    assert ctx.requested == [{"identity_id": "account"}]
    assert calls == ["FR", "en", "de", "bad", "en-US", "xx"]
    assert plan.facts == {
        "language_values": 6, "invalid": 0, "noncanonical": 1,
        "malformed": 6, "normalization_failures": 2,
    }
    assert plan.warnings == (
        "Langue non textuelle : notice n°1",
        "Langue non canonique : notice n°1",
        "Langue non textuelle : notice n°2",
        "Langue non vérifiable : notice n°2",
        "Langue non vérifiable : notice n°2",
    )
    assert rows[0]["languages"] == original[0]
    assert rows[1] == original[1]

    control = Control()
    result = tool.run(plan, control)
    assert result.changed == 0
    assert result.ok is True
    assert control.checks == 2
    assert control.events == [{"message": plan.summary}]
    assert result.facts == plan.facts
    exported = json.dumps({
        "summary": plan.summary,
        "warnings": result.warnings,
        "facts": result.facts,
        "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "secret.epub", "BOOK_ONE", "BOOK_TWO"):
        assert secret not in exported


def test_nested_bad_values_do_not_invent_languages_or_execute_str():
    ctx = Context([
        {
            "title": "PRIVATE_TITLE",
            "language": None,
            "languages": [StringBomb(), ["fr"], True, 17, {}, ""],
            "metadata": {"langs": {"PRIVATE_SECRET": "en"}},
        }
    ])
    plan = audits.LanguageAuditTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id=None)
    )
    assert plan.facts == {
        "language_values": 0, "invalid": 0, "noncanonical": 0,
        "malformed": 6, "normalization_failures": 0,
    }
    assert plan.warnings == ("Langue non textuelle : notice n°1",)
    assert "PRIVATE" not in plan.summary + str(plan.warnings)


def test_empty_language_catalogue_remains_a_precomputed_indeterminate_audit():
    ctx = Context([])
    plan = audits.LanguageAuditTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id="account")
    )
    assert plan.facts == {
        "language_values": 0, "invalid": 0, "noncanonical": 0,
        "malformed": 0, "normalization_failures": 0,
    }
    assert plan.warnings == ()
    control = Control()
    assert audits.LanguageAuditTool().run(plan, control).ok is True
    assert control.events == [{"message": plan.summary}]
    assert all("total" not in e and "current" not in e for e in control.events)
