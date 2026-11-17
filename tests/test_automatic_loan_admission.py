"""Initial loan admission remains scoped and precedes artifacts/provider I/O."""
from dataclasses import replace
from types import SimpleNamespace as NS
import pytest
from cifereca.core.loan_acquisition_admission import resolve
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service


LOGGER = NS(info=lambda *_a, **_kw: None, warning=lambda *_a, **_kw: None)


def _setup(tmp_path, *, bound=True, linked=False):
    service = _service(tmp_path)
    account = Account(provider_id='paris', credential_id='source-a', identity_id='default',
                      unique_id='linked-a' if linked else '', is_primary=not linked)
    service.accounts = [account]
    if bound:
        service.storage.provider_connection_put({'provider_id': 'paris', 'credential_id': 'source-a', 'identity_id': 'default'})
    loan = Loan(id='loan-0', record_id='edition-0', provider='PNB', medium='digital', title='Synthetic',
                source_provider='paris', source_credential='source-a',
                source_account_id='linked-a' if linked else 'primary', identity_id='default')
    service.current_identity = service.identity_by_id('alex')
    return service, loan


def _forbid_work(service, monkeypatch):
    def forbidden(*_a, **_kw): pytest.fail('initial admission cannot mutate, inspect artifacts or contact a provider')
    for name in ('acquire_digital_loan', 'execute_acquisition_request', '_kobo_adapter', 'catalog_index_path'):
        monkeypatch.setattr(service, name, forbidden, raising=name != "_kobo_adapter")
    for name in ('catalog_entries', 'acquisition_put', 'entitlement_put'):
        monkeypatch.setattr(service.storage, name, forbidden)


@pytest.mark.parametrize('count', [0, 1, 4])
def test_admitted_context_uses_connection_owner_and_existing_right_only(tmp_path, monkeypatch, count):
    service, loan = _setup(tmp_path)
    try:
        if count:
            service._persist_active_loans('default', [replace(loan, id=f'loan-{i}', record_id=f'edition-{i}')
                                                     for i in range(count)])
        before = service.storage.entitlements(provider_id='paris', state='active')
        expected = next((r['entitlement_id'] for r in before if r['loan_id'] == loan.id), None)
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None and result.identity.identity_id == 'default'
        assert result.entitlement_id == expected
        assert service.current_identity.identity_id == 'alex'
        assert service.storage.entitlements(provider_id='paris', state='active') == before
        assert service.storage.acquisitions() == []
    finally:
        service.close()


@pytest.mark.parametrize(('scenario', 'code'), [
    ('unbound', 'adept_identity_unproven'),
    ('borrower', 'adept_borrower_binding_required'),
    ('foreign_ledger', 'adept_loan_identity_conflict'),
    ('foreign_entitlement', 'adept_loan_identity_conflict'),
])
def test_refused_context_preserves_public_outcome_and_never_falls_back_to_ui(tmp_path, monkeypatch, scenario, code):
    service, loan = _setup(tmp_path, bound=scenario != 'unbound', linked=scenario == 'borrower')
    try:
        if scenario == 'foreign_ledger':
            service.storage.acquisition_put({'acquisition_id': 'prior', 'provider_id': 'paris',
                'credential_id': 'source-b', 'account_id': 'primary', 'identity_id': 'alex',
                'item_id': loan.record_id, 'status': 'complete', 'strategy': 'loan_auto',
                'metadata': {'provider_loan_id': loan.id}})
        elif scenario == 'foreign_entitlement':
            service._persist_active_loans('alex', [replace(loan, source_credential='source-b', identity_id='alex')])
        before_rights = service.storage.entitlements(provider_id='paris', state='active')
        before_ledger = service.storage.acquisitions()
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal['status'] == 'attention_required'
        assert result.refusal['code'] == code and result.refusal['retry'] == 'explicit_only'
        # Exercise the real caller bridge too: it returns the historical dict,
        # not the private context and not a permission to reach later stages.
        assert service._acquire_active_loan_automatically_guarded(loan) == result.refusal
        assert service.current_identity.identity_id == 'alex'
        assert service.storage.entitlements(provider_id='paris', state='active') == before_rights
        assert service.storage.acquisitions() == before_ledger
    finally:
        service.close()


@pytest.mark.parametrize("hook", [
    "identity_by_id", "_paris_adept_identity_for_loan",
    "entitlements", "acquisitions",
])
def test_admission_refuses_source_drift_after_each_external_hook(
    tmp_path, monkeypatch, hook,
):
    service, loan = _setup(tmp_path)
    try:
        owner = service if hook in {"identity_by_id", "_paris_adept_identity_for_loan"} else service.storage
        original = getattr(owner, hook)

        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            loan.source_credential = "source-other"
            return result

        monkeypatch.setattr(owner, hook, mutate)
        denied = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert denied.identity is None
        assert denied.entitlement_id is None
        assert denied.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


@pytest.mark.parametrize("hook", [
    "identity_by_id", "_paris_adept_identity_for_loan",
    "entitlements", "acquisitions",
])
@pytest.mark.parametrize("field,value", [
    ("id", "foreign-loan"),
    ("record_id", "foreign-edition"),
])
def test_admission_refuses_loan_key_drift_after_external_hook(
    tmp_path, monkeypatch, hook, field, value,
):
    service, loan = _setup(tmp_path)
    try:
        owner = service if hook in {
            "identity_by_id", "_paris_adept_identity_for_loan",
        } else service.storage
        original = getattr(owner, hook)

        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            setattr(loan, field, value)
            return result

        monkeypatch.setattr(owner, hook, mutate)
        denied = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert denied.identity is None and denied.entitlement_id is None
        assert denied.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


class _HostileLoanCoordinate:
    def __str__(self):
        raise AssertionError("untrusted provider coordinate must not be coerced")

    def __bool__(self):
        raise AssertionError("untrusted provider coordinate must not be used as truth")


class _HostileLoanString(str):
    def lower(self):
        raise AssertionError("untrusted string subclass must not select ADEPT grant")


@pytest.mark.parametrize("field,new_value", [
    ("source_provider", "future-library"),
    ("source_credential", "source-b"),
    ("source_account_id", "borrower-b"),
    ("source_provider", _HostileLoanString("paris")),
])
def test_admission_refuses_source_drift_during_connection_lookup(
    tmp_path, monkeypatch, field, new_value,
):
    service, loan = _setup(tmp_path)
    try:
        original_lookup = service.storage.identity_for_connection

        def drifting_lookup(provider_id, credential_id):
            owner = original_lookup(provider_id, credential_id)
            setattr(loan, field, new_value)
            return owner

        monkeypatch.setattr(service.storage, "identity_for_connection", drifting_lookup)
        def forbidden(*_args, **_kwargs):
            pytest.fail("changed loan scope must not reach borrower or rights")
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", forbidden)
        monkeypatch.setattr(service.storage, "entitlements", forbidden)
        monkeypatch.setattr(service.storage, "acquisitions", forbidden)
        refused = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert refused.identity is None
        assert refused.entitlement_id is None
        assert refused.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize("field,bad", [
    ("source_provider", _HostileLoanCoordinate()),
    ("source_provider", _HostileLoanString("paris")),
    ("source_provider", None),
    ("source_provider", ""),
    ("source_credential", _HostileLoanCoordinate()),
    ("source_credential", _HostileLoanString("source-a")),
    ("source_credential", None),
    ("source_credential", ""),
    ("source_account_id", _HostileLoanCoordinate()),
    ("source_account_id", _HostileLoanString("primary")),
    ("source_account_id", 0),
    ("source_account_id", False),
    ("source_account_id", []),
])
def test_admission_refuses_non_native_source_before_storage_or_provider(
    tmp_path, monkeypatch, field, bad,
):
    service, loan = _setup(tmp_path)
    try:
        malformed = replace(loan, **{field: bad})
        def forbidden(*_args, **_kwargs):
            pytest.fail("untrusted source must not query connection, grant or rights")
        monkeypatch.setattr(service.storage, "identity_for_connection", forbidden)
        monkeypatch.setattr(service.storage, "entitlements", forbidden)
        monkeypatch.setattr(service.storage, "acquisitions", forbidden)
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", forbidden)
        result = resolve(service, malformed, _TRACE_LOG=LOGGER)
        assert result.identity is None
        assert result.entitlement_id is None
        assert result.refusal["status"] == "attention_required"
        assert result.refusal["code"] == "adept_identity_unproven"
        assert result.refusal["reason"] == "source_scope_unproven"
        assert result.refusal["retry"] == "explicit_only"
    finally:
        service.close()


@pytest.mark.parametrize("field,bad", [
    ("id", None),
    ("id", ""),
    ("id", False),
    ("id", 0),
    ("id", _HostileLoanCoordinate()),
    ("id", _HostileLoanString("loan-0")),
    ("record_id", False),
    ("record_id", 0),
    ("record_id", _HostileLoanCoordinate()),
    ("record_id", _HostileLoanString("edition-0")),
])
def test_admission_rejects_untrusted_loan_keys_before_owner_lookup(
    tmp_path, monkeypatch, field, bad,
):
    service, loan = _setup(tmp_path)
    try:
        malformed = replace(loan, **{field: bad})

        def forbidden(*_args, **_kwargs):
            pytest.fail("malformed loan key reached storage, grants or rights")

        monkeypatch.setattr(service.storage, "identity_for_connection", forbidden)
        monkeypatch.setattr(service.storage, "entitlements", forbidden)
        monkeypatch.setattr(service.storage, "acquisitions", forbidden)
        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", forbidden)
        result = resolve(service, malformed, _TRACE_LOG=LOGGER)
        assert result.identity is None and result.entitlement_id is None
        assert result.refusal["status"] == "attention_required"
        assert result.refusal["reason"] == "source_scope_unproven"
        assert result.refusal["code"] == (
            "adept_loan_identity_conflict"
            if field == "id" and bad == "" else "adept_identity_unproven"
        )
        assert result.refusal["retry"] == "explicit_only"
        if field == "id":
            assert result.refusal["loan_id"] == (bad if type(bad) is str else None)
    finally:
        service.close()


def test_admission_rejects_mutated_hostile_loan_key_after_owner_lookup(
    tmp_path, monkeypatch,
):
    service, loan = _setup(tmp_path)
    try:
        original = service.storage.identity_for_connection

        def mutate(provider_id, credential_id):
            owner = original(provider_id, credential_id)
            loan.id = _HostileLoanCoordinate()
            return owner

        monkeypatch.setattr(service.storage, "identity_for_connection", mutate)
        monkeypatch.setattr(service.storage, "entitlements", lambda *_a, **_kw: pytest.fail(
            "hostile loan key cannot reach rights"
        ))
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.identity is None and result.entitlement_id is None
        assert result.refusal == {
            "status": "attention_required", "loan_id": "loan-0",
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize(("field", "replacement"), [
    ("id", _HostileLoanCoordinate()),
    ("id", "foreign-loan"),
    ("record_id", "foreign-edition"),
    ("source_account_id", "other-borrower"),
    ("source_credential", "other-credential"),
])
def test_borrower_refusal_callback_drift_never_publishes_foreign_loan(
    tmp_path, monkeypatch, field, replacement,
):
    service, loan = _setup(tmp_path)
    try:
        def refused_and_drifted(*_args, **_kwargs):
            setattr(loan, field, replacement)
            return None, "adept_borrower_binding_required"

        monkeypatch.setattr(service, "_paris_adept_identity_for_loan", refused_and_drifted)

        def no_rights(*_args, **_kwargs):
            pytest.fail("borrower refusal must not inspect rights or acquisitions")

        monkeypatch.setattr(service.storage, "entitlements", no_rights)
        monkeypatch.setattr(service.storage, "acquisitions", no_rights)
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.identity is None and result.entitlement_id is None
        assert result.refusal == {
            "status": "attention_required",
            "loan_id": "loan-0" if field == "id" and type(replacement) is not str
                       else loan.id,
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed",
            "retry": "explicit_only",
        }
    finally:
        service.close()


def test_unchanged_borrower_refusal_preserves_public_code_and_pinned_id(
    tmp_path, monkeypatch,
):
    service, loan = _setup(tmp_path)
    try:
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            lambda *_a, **_kw: (None, "adept_borrower_binding_required"),
        )
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal == {
            "status": "attention_required",
            "loan_id": "loan-0",
            "code": "adept_borrower_binding_required",
            "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize(("field", "replacement"), [
    ("id", "different-loan"),
    ("id", _HostileLoanCoordinate()),
    ("source_provider", "different-provider"),
    ("source_provider", _HostileLoanString("paris")),
    ("source_credential", "different-credential"),
    ("source_account_id", "different-borrower"),
    ("record_id", "different-edition"),
])
def test_lock_acquisition_refuses_drift_before_guarded_provider_path(
    tmp_path, monkeypatch, field, replacement,
):
    import cifereca.services as services

    service, loan = _setup(tmp_path)
    try:
        class DriftingLock:
            def __init__(self, _path):
                pass

            def __enter__(self):
                setattr(loan, field, replacement)
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(services, "InterProcessLock", DriftingLock)
        monkeypatch.setattr(services, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")

        def no_guarded(*_args, **_kwargs):
            pytest.fail("a changed loan must not run under a stale process lock")

        monkeypatch.setattr(service, "_acquire_active_loan_automatically_guarded", no_guarded)
        result = service._acquire_active_loan_automatically(loan)
        assert result == {
            "status": "attention_required", "loan_id": "loan-0",
            "code": "adept_identity_unproven",
            "reason": "source_scope_changed", "retry": "explicit_only",
        }
    finally:
        service.close()


def test_busy_automatic_loan_lock_never_echoes_mutated_hostile_id(
    tmp_path, monkeypatch,
):
    import cifereca.services as services

    service, loan = _setup(tmp_path)
    try:
        class BusyAfterMutation:
            def __init__(self, _path):
                pass

            def __enter__(self):
                loan.id = _HostileLoanCoordinate()
                raise services.LockBusyError("locked")

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(services, "InterProcessLock", BusyAfterMutation)
        monkeypatch.setattr(services, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
        monkeypatch.setattr(
            service, "_acquire_active_loan_automatically_guarded",
            lambda *_a, **_kw: pytest.fail("busy lock may never reach provider"),
        )
        assert service._acquire_active_loan_automatically(loan) == {
            "status": "attention_required", "loan_id": "loan-0",
            "code": "acquisition_busy", "retry": "explicit_only",
        }
    finally:
        service.close()


def test_outer_loan_lock_rejects_hostile_provider_before_bnf_comparison(
    tmp_path, monkeypatch,
):
    import cifereca.services as services

    class HostileProvider:
        def __eq__(self, other):
            raise AssertionError("provider-owned equality must never run")

    service, loan = _setup(tmp_path)
    try:
        loan.source_provider = HostileProvider()
        monkeypatch.setattr(
            services, "InterProcessLock",
            lambda *_a, **_kw: pytest.fail("hostile provider must never acquire lock"),
        )
        assert service._acquire_active_loan_automatically(loan) == {
            "status": "attention_required", "loan_id": None,
            "code": "adept_loan_identity_unproven", "retry": "explicit_only",
        }
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-library"])
@pytest.mark.parametrize("borrower", [None, "", False, 0, object()])
def test_preget_unproven_positive_borrower_denied_before_rights(
    tmp_path, monkeypatch, provider, borrower,
):
    """A literal None refusal code is not evidence of an ADEPT borrower."""
    from unittest.mock import Mock

    service, loan = _setup(tmp_path)
    try:
        if provider != "paris":
            loan.source_provider = provider
            service.storage.provider_connection_put({
                "provider_id": provider, "credential_id": "source-a",
                "identity_id": "default", "enabled": True,
            })
        borrower_resolver = Mock(return_value=(borrower, None))
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan", borrower_resolver,
        )
        no_rights = Mock(side_effect=AssertionError(
            "missing borrower must not inspect rights or stage ACSM"
        ))
        monkeypatch.setattr(service.storage, "entitlements", no_rights)
        monkeypatch.setattr(service.storage, "acquisitions", no_rights)

        admission = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert admission.identity is None
        assert admission.entitlement_id is None
        assert admission.refusal == {
            "status": "attention_required", "loan_id": "loan-0",
            "code": "adept_borrower_ambiguous", "retry": "explicit_only",
        }
        borrower_resolver.assert_called_once_with(
            loan, connection_identity_id="default",
        )
        no_rights.assert_not_called()
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-library"])
def test_preget_exceptional_borrower_resolver_failure_is_closed(
    tmp_path, monkeypatch, provider,
):
    from unittest.mock import Mock

    service, loan = _setup(tmp_path)
    try:
        if provider != "paris":
            loan.source_provider = provider
            service.storage.provider_connection_put({
                "provider_id": provider, "credential_id": "source-a",
                "identity_id": "default", "enabled": True,
            })
        borrower_resolver = Mock(side_effect=RuntimeError(
            "private Adobe credential must not escape"
        ))
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan", borrower_resolver,
        )
        no_rights = Mock(side_effect=AssertionError(
            "failed borrower resolver must not inspect rights"
        ))
        monkeypatch.setattr(service.storage, "entitlements", no_rights)
        monkeypatch.setattr(service.storage, "acquisitions", no_rights)

        admission = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert admission.refusal == {
            "status": "attention_required", "loan_id": "loan-0",
            "code": "adept_borrower_ambiguous", "retry": "explicit_only",
        }
        assert "private" not in repr(admission.refusal)
        assert admission.identity is None
        no_rights.assert_not_called()
    finally:
        service.close()
