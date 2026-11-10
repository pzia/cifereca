"""P1 #4981: installed Notice C text consolidator subprocess ABI and isolation.

These are real isolated Python worker invocations on Linux, not mocked
provider output. No supplier HTTP, SQLite writer or GTK is involved.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from cifereca.core.notice_canonical_text_schema import CanonicalTextSchema
from cifereca.core.notice_consolidator_selection import ConsolidatorDeclaration
from cifereca.core.notice_consolidator_runner import (
    InstalledTextConsolidator, run_registered_text_consolidator,
)

TARGET = "isbn:9780306406157"
FOREIGN = "isbn:9780306406158"
DECLARATION = ConsolidatorDeclaration(
    "plugin.age.alpha", "age", "fr:editions", "1.0.0", "schema.age.v1",
)
OTHER = ConsolidatorDeclaration(
    "plugin.age.beta", "age", "fr:editions", "2.0.0", "schema.age.v2",
)
SCHEMA = CanonicalTextSchema(
    "age", "fr:editions", "plugin.age.alpha", "1.0.0",
    "schema.age.v1", 32, True,
)
# No imports, no permissions: type-specific choice belongs to installed plugin.
SOURCE = '''
def consolidate(facts):
    rows = facts["exact"] or facts["inherited"]
    if not rows:
        return None
    row = rows[0]
    return {
        "canonical_type": facts["canonical_type"],
        "target_edition_id": facts["target_edition_id"],
        "schema_id": facts["schema_id"],
        "value": row["value"],
        "inherited": not bool(facts["exact"]),
        "source_evidence_keys": [[
            row["source_id"], row["edition_id"], row["evidence_id"]
        ]],
    }
'''


def installed(source=SOURCE, **changes):
    row = InstalledTextConsolidator(
        "age", "fr:editions", "plugin.age.alpha", "1.0.0",
        "schema.age.v1", source, sha256(source.encode()).hexdigest(),
    )
    return replace(row, **changes)


def fact(value="13+", *, edition=TARGET):
    return {
        "source_id": "bnf", "edition_id": edition,
        "evidence_id": "proof1", "canonical_type": "age",
        "source_revision": "rev1", "value": value,
    }


def invoke(*, source=SOURCE, **overrides):
    options = dict(
        declarations=(OTHER, DECLARATION),
        text_schemas=(SCHEMA,),
        installed=installed(source),
        canonical_type="age",
        scope="fr:editions",
        selected_plugin_id="plugin.age.alpha",
        selected_version="1.0.0",
        target_edition_id=TARGET,
        source_facts=(fact(),),
        attested_strong_links={},
    )
    options.update(overrides)
    return run_registered_text_consolidator(**options)


def test_real_worker_selects_one_version_and_attests_exact_edition():
    result = invoke()
    assert result is not None
    assert (result.value, result.inherited, result.plugin_id,
            result.plugin_version, result.source_evidence_keys) == (
                "13+", False, "plugin.age.alpha", "1.0.0",
                (("bnf", TARGET, "proof1"),),
            )
    assert invoke() == result  # Stable across independent worker processes.


def test_related_strong_evidence_remains_inherited_and_needs_schema_permission():
    result = invoke(
        source_facts=(fact("10+", edition=FOREIGN),),
        attested_strong_links={FOREIGN: "strong:confirmed"},
    )
    assert result.value == "10+" and result.inherited
    assert result.source_evidence_keys == (("bnf", FOREIGN, "proof1"),)
    with pytest.raises(ValueError, match="inheritance_forbidden"):
        invoke(
            source_facts=(fact("10+", edition=FOREIGN),),
            attested_strong_links={FOREIGN: "strong:confirmed"},
            text_schemas=(replace(SCHEMA, inherited_allowed=False),),
        )


@pytest.mark.parametrize("changes,reason", [
    ({"selected_plugin_id": None, "selected_version": None},
     "consolidator_not_selected"),
    ({"selected_plugin_id": "plugin.age.beta", "selected_version": "2.0.0"},
     "canonical_text_schema_not_installed"),
    ({"selected_version": "1.0.1"}, "consolidator_not_selected"),
    ({"declarations": (DECLARATION, DECLARATION)},
     "ambiguous_consolidator_declaration"),
    ({"installed": installed(plugin_id="another.plugin")},
     "installed_consolidator_mismatch"),
    ({"installed": installed(source_sha256="0" * 64)},
     "installed_consolidator_mismatch"),
])
def test_scope_selection_registry_and_source_hash_fail_closed(changes, reason):
    with pytest.raises(ValueError, match=reason):
        invoke(**changes)


def test_unlinked_source_is_not_sent_to_worker(monkeypatch):
    from cifereca.core import notice_consolidator_runner as runner

    def forbidden(_):
        raise AssertionError("unadmitted evidence must never execute")

    monkeypatch.setattr(runner, "_run_worker", forbidden)
    assert invoke(source_facts=(fact(edition=FOREIGN),)) is None


def test_colliding_and_forged_evidence_fail_before_plugin_execution():
    with pytest.raises(ValueError, match="conflicting_source_evidence"):
        invoke(source_facts=(fact("13+"), fact("12+")))
    forged = '''
def consolidate(facts):
    return {
        "canonical_type": "age",
        "target_edition_id": "isbn:someone-else",
        "schema_id": "schema.age.v1",
        "value": "13+", "inherited": False,
        "source_evidence_keys": [["bnf", "isbn:someone-else", "proof1"]],
    }
'''
    with pytest.raises(ValueError, match="consolidator_result_scope_mismatch"):
        invoke(source=forged)


@pytest.mark.parametrize("source", [
    'def consolidate(facts):\n    return open("/etc/passwd").read()\n',
    'import socket\ndef consolidate(facts):\n    return None\n',
    'import sqlite3\ndef consolidate(facts):\n    return None\n',
    'def consolidate(facts):\n    raise ValueError("secret:PRIVATE_TOKEN")\n',
    'def consolidate(facts):\n    return {"status": "borrowed"}\n',
    'def consolidate(facts):\n    return "unexpected-output"\n',
])
def test_worker_cannot_publish_io_or_unverified_plugin_output(source):
    with pytest.raises(ValueError, match="consolidator_(execution_failed|result_shape)|invalid_consolidator_result_shape"):
        invoke(source=source)


def test_worker_timeout_is_closed_without_retry_or_result():
    with pytest.raises(ValueError, match="consolidator_execution_unavailable"):
        invoke(source="def consolidate(facts):\n    while True:\n        pass\n")


def test_worker_does_not_leak_large_output_or_exception_to_core():
    secret = "PRIVATE_SESSION_TOKEN"
    with pytest.raises(ValueError) as exc:
        invoke(source='def consolidate(facts):\n    return "' + secret + '" * 20000\n')
    assert secret not in str(exc.value)


def test_double_run_detects_a_non_deterministic_worker(monkeypatch):
    from cifereca.core import notice_consolidator_runner as runner
    values = [None, {"wrong": True}]

    def alternate(_):
        return values.pop(0)

    monkeypatch.setattr(runner, "_run_worker", alternate)
    with pytest.raises(ValueError, match="non_deterministic_consolidator"):
        invoke()
    assert values == []
