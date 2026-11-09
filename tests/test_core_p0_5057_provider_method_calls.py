"""P0 #5057: direct executable provider-specific method calls in Core."""
from scripts.ci_provider_core_coupling_inventory import inspect_source
from scripts.ci_provider_coupling_delta_gate import added_signals


def test_named_provider_methods_are_executable_coupling():
    source = """
def apply(service, broker):
    service.sync_kobo_backup()
    broker.numilog_selected_acquisition()
    service.bnf_borrow()
    service.biblioaccess_refresh()
"""
    report = inspect_source(source)
    assert report["provider_method_calls"] == [
        {"line": 3, "providers": ["kobo"]},
        {"line": 4, "providers": ["numilog"]},
        {"line": 5, "providers": ["bnf"]},
        {"line": 6, "providers": ["biblioaccess"]},
    ]
    assert added_signals("pass\n", source) == {
        ("provider_method_calls", "kobo"): 1,
        ("provider_method_calls", "numilog"): 1,
        ("provider_method_calls", "bnf"): 1,
        ("provider_method_calls", "biblioaccess"): 1,
    }


def test_passive_method_references_and_similar_words_do_not_trip_gate():
    source = """
label = service.kobo_backup
token = "service.sync_kobo_backup()"
service.sync_kobold_backup()
service.get_numilogger()
service.search_provider()
service.provider_backup()
"""
    assert inspect_source(source)["provider_method_calls"] == []


def test_method_name_signal_is_multiplicity_monotone_and_independent_of_lines():
    prior = "service.sync_kobo_backup()\n"
    assert added_signals(prior, "\n" + prior) == {}
    assert added_signals(prior, prior + "service.sync_kobo_backup()\n") == {
        ("provider_method_calls", "kobo"): 1,
    }
