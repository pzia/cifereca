"""P0 #5048: deferred provider reflection is executable Core coupling."""
from scripts.ci_provider_core_coupling_inventory import inspect_source
from scripts.ci_provider_coupling_delta_gate import added_signals


def test_invoked_local_alias_to_provider_method_is_detected():
    src = '''
def run(service):
    action = getattr(service, "sync_kobo_backup")
    return action()
'''
    assert inspect_source(src)["provider_dynamic_calls"] == [
        {"line": 3, "providers": ["kobo"]},
    ]
    assert added_signals("def run(service):\n    pass\n", src) == {
        ("provider_dynamic_calls", "kobo"): 1,
    }


def test_bnf_and_numilog_deferred_calls_are_blocked_too():
    src = '''
def run(service):
    loan = getattr(service, "bnf_borrow")
    download = getattr(service, "numilog_download")
    return loan(), download()
'''
    assert inspect_source(src)["provider_dynamic_calls"] == [
        {"line": 3, "providers": ["bnf"]},
        {"line": 4, "providers": ["numilog"]},
    ]


def test_uninvoked_metadata_reflection_is_not_executable():
    src = '''
def run(service):
    value = getattr(service, "sync_kobo_backup")
    return value.__name__
'''
    assert inspect_source(src)["provider_dynamic_calls"] == []


def test_shadowed_getattr_is_not_builtin_reflection():
    src = '''
def run(service, getattr):
    f = getattr(service, "sync_kobo_backup")
    f()
'''
    assert inspect_source(src)["provider_dynamic_calls"] == []


def test_reassigned_alias_does_not_prove_vendor_method_invocation():
    src = '''
def run(service):
    f = getattr(service, "sync_kobo_backup")
    f = service.generic_operation
    f()
'''
    assert inspect_source(src)["provider_dynamic_calls"] == []


def test_parameter_shadow_and_other_function_call_not_attributed():
    src = '''
def store(service):
    f = getattr(service, "sync_kobo_backup")
    return f
def use(f):
    f()
'''
    assert inspect_source(src)["provider_dynamic_calls"] == []


def test_direct_getattr_invocation_is_counted_once():
    src = 'getattr(service, "sync_kobo_backup")()\n'
    assert inspect_source(src)["provider_dynamic_calls"] == [
        {"line": 1, "providers": ["kobo"]},
    ]


def test_non_vendor_generic_callback_not_counted():
    src = '''
def run(service):
    f = getattr(service, "generic_transfer")
    return f()
'''
    assert inspect_source(src)["provider_dynamic_calls"] == []
