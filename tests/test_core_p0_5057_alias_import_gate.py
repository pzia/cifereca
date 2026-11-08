"""P0 #5057: precision and regressions for provider aliases and dynamic imports."""
from __future__ import annotations

from scripts.ci_provider_core_coupling_inventory import inspect_source
from scripts.ci_provider_coupling_delta_gate import added_signals, signal_counts


def test_provider_package_id_variants_and_paris_are_not_invisible():
    result = inspect_source('''
if provider == "provider-kobo":
    pass
if provider in {"provider-numilog", "provider-paris", "provider-bnf-pret"}:
    pass
match provider:
    case "bnf_pret" | "bnf_catalogue":
        pass
''')
    assert [row["providers"] for row in result["comparisons"]] == [
        ["provider-kobo"],
        ["provider-bnf-pret", "provider-numilog", "provider-paris"],
        ["bnf_catalogue", "bnf_pret"],
    ]


def test_immutable_module_constants_are_counted_in_comparisons_and_membership():
    result = inspect_source('''
KOBO_PROVIDER = "kobo"
BNF_PRET: str = "bnf-pret"
if provider == KOBO_PROVIDER:
    pass
if provider in (BNF_PRET, "numilog"):
    pass
''')
    assert [row["providers"] for row in result["comparisons"]] == [
        ["kobo"], ["bnf-pret", "numilog"],
    ]


def test_reassigned_and_shadowed_constants_fail_closed_against_false_positive():
    result = inspect_source('''
KOBO = "kobo"
KOBO = read_config()
if provider == KOBO:
    pass
PARIS = "paris"
def f(PARIS):
    return provider == PARIS
''')
    assert result["comparisons"] == []


def test_dynamic_importlib_and_builtin_import_are_detected():
    result = inspect_source('''
import importlib as loader
from importlib import import_module as load
module1 = loader.import_module("cifereca.kobo.executor")
module2 = load("cifereca.numilog.private")
module3 = __import__("cifereca.bnf.reader")
''')
    assert [row["providers"] for row in result["imports"]] == [
        ["kobo"], ["numilog"], ["bnf"],
    ]


def test_similarly_named_unbound_calls_and_text_are_not_dispatch():
    result = inspect_source('''
def import_module(name):
    return name
x = import_module("cifereca.kobo.executor")
notice = "provider-kobo"
x = getattr(service, "provider-kobo")
''')
    assert result == {"imports": [], "comparisons": []}


def test_identifier_parts_not_substring_for_dynamic_imports():
    result = inspect_source('''
import importlib
importlib.import_module("package.kobold.impl")
importlib.import_module("package.numeric.helper")
importlib.import_module(get_dynamic_module())
''')
    assert result == {"imports": [], "comparisons": []}


def test_existing_debt_is_stable_and_new_alias_trips_delta():
    old = 'if provider == "kobo":\n    pass\n'
    same = 'if  provider  ==  "kobo":\n    pass\n'
    added = same + 'if provider == "provider-kobo":\n    pass\n'
    assert added_signals(old, same) == {}
    assert added_signals(old, added) == {("comparisons", "provider-kobo"): 1}
    assert signal_counts(old)[("comparisons", "kobo")] == 1


def test_new_dynamic_import_trips_monotone_gate():
    old = "import importlib\n"
    new = old + 'importlib.import_module("cifereca.kobo.executor")\n'
    assert added_signals(old, new) == {("imports", "kobo"): 1}
