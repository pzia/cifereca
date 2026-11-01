"""#4048: no voluntary ADEPT/PNB return even on direct Core/broker calls.

Synthetic only. Checks that an old programmatic caller cannot initiate
provider/ADEPT I/O, SQL checkpoint writes or inventory mutation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import AdeptLoanBrokerImpl
from cifereca.services import LibraryService, AttentionRequired
from cifereca.core import (
    provider_loan_return, return_adept_orchestrator, adept_acquisition_return,
)


class NoSideEffects:
    def __getattribute__(self, name):
        raise AssertionError("legacy return contacted Core state: " + name)


@pytest.mark.parametrize("method", [
    "return_digital", "return_loan_prefer_adept", "verify_loan_return",
])
def test_service_return_facades_never_read_or_mutate_existing_state(method):
    result = getattr(LibraryService, method)(NoSideEffects(), object())
    assert result["code"] == "loan_return_removed"
    assert result["status"] in ("blocked", "verification_unavailable")
    assert result["retry"] == "none"


def test_adept_service_facade_denies_before_lock_storage_or_runtime():
    with pytest.raises(AttentionRequired) as error:
        LibraryService.return_adept_acquisition(NoSideEffects(), "old-acquisition")
    assert error.value.code == "loan_return_removed"


def test_direct_drm_return_cannot_invoke_a_stale_runtime_plugin():
    with pytest.raises(AttentionRequired) as error:
        LibraryService.execute_drm_mutation(
            NoSideEffects(), NS(operation="return", metadata={"private": "SECRET"})
        )
    assert error.value.code == "loan_return_removed"


def test_public_adept_broker_cannot_forward_old_return_command():
    with pytest.raises(AttentionRequired) as error:
        AdeptLoanBrokerImpl.return_acquisition(
            NS(service=NoSideEffects()), "old-acquisition"
        )
    assert error.value.code == "loan_return_removed"


def test_provider_only_helper_never_reaches_session_even_on_direct_call():
    result = provider_loan_return.return_digital(
        NoSideEffects(), object(), explicit_retry=True, _TRACE_LOG=None
    )
    assert result == {"status": "blocked", "code": "loan_return_removed", "retry": "none"}


def test_adobe_first_helper_cannot_restart_a_historical_checkpoint():
    result = return_adept_orchestrator.return_loan_prefer_adept(
        NoSideEffects(), object(), uuid=None, return_ref=None,
        adept_return_key=None, public_state=None, AttentionRequired=AttentionRequired,
        _TRACE_LOG=None,
    )
    assert result == {"status": "blocked", "code": "loan_return_removed", "retry": "none"}


def test_adept_acquisition_helper_refuses_without_even_reading_origin():
    with pytest.raises(AttentionRequired) as error:
        adept_acquisition_return.return_adept_acquisition(
            NoSideEffects(), "old-acquisition", AttentionRequired=AttentionRequired,
            ProviderConfigurationError=None, DrmMutationRequest=None, time=None,
        )
    assert error.value.code == "loan_return_removed"



def test_coreui_facades_no_longer_publish_voluntary_return_callbacks():
    import ast
    from pathlib import Path
    gui = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
    forbidden = {
        "confirm_return", "_return_loan", "_return_loan_pnb",
        "_return_loan_result", "return_adept_from_detail",
    }
    for filename in ("main_window.py", "loans_controller.py"):
        tree = ast.parse((gui / filename).read_text(encoding="utf-8"))
        found = {node.name for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
        assert not (found & forbidden), (filename, found & forbidden)
    for filename in ("detail_panel.py", "detail_facets_controller.py"):
        source = (gui / filename).read_text(encoding="utf-8")
        assert "return_adept_from_detail(" not in source
        assert "confirm_return(" not in source



def test_legacy_lending_return_is_never_capability_or_admissible_action():
    from cifereca.plugin_api.lending_actions import assess_lending_action
    from cifereca.plugin_api.lending_capabilities import assess_lending_operation
    # Do not even instantiate a provider: retirement must be decided before
    # any descriptor/session getter, loan raw field, or account lookup.
    operation = assess_lending_operation(
        "loan.return", manifest_capabilities=["lending.return"],
        descriptor=NoSideEffects(), session=NoSideEffects(),
        account=NoSideEffects(),
    )
    assert operation.availability == "unknown"
    assert operation.reason_code == "operation_unknown"
    admission = assess_lending_action(
        "loan.return", fact=operation, target=NoSideEffects(),
        account=NoSideEffects(), confirmed=True, permission_attested=True,
    )
    assert admission.status == "unsupported"
    assert admission.reason_code == "operation_unavailable"



def test_historical_return_provider_flag_is_not_projected_as_capability():
    from cifereca.plugin_api.capability_matrix import declared_provider_matrix
    from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
    descriptor = ProviderDescriptor(
        id="paris", display_name="Fictif", description="Test",
        state="ready", capabilities=ProviderCapabilities(
            loans=True, return_loan=True, renew_loan=True,
        ),
    )
    matrix = declared_provider_matrix(descriptor)
    operations = {row.operation for row in matrix.observations}
    assert "loan.return" not in operations
    assert "loan.renew" in operations
    assert "loan.inventory" in operations
