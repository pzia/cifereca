"""P0 #5054: the legacy Numilog direct GET cannot bypass live Core admission."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


SOURCE = (Path(__file__).resolve().parents[1]
          / "src/cifereca/core/numilog_direct_acquisition.py")


def _function(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "acquire_numilog_direct")
    return next(node for node in ast.walk(owner)
                if isinstance(node, ast.FunctionDef)
                and node.name == name)


def test_direct_get_receives_current_core_admission_without_unguarded_fallback():
    text = SOURCE.read_text(encoding="utf-8")
    assert "core_admission_current=delivery_admission_current" in text
    direct_calls = [
        node for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "download_direct"
    ]
    assert len(direct_calls) == 1
    assert any(keyword.arg == "core_admission_current"
               for keyword in direct_calls[0].keywords)
    lease = _function("delivery_admission_current")
    lease_text = ast.get_source_segment(text, lease)
    assert "session=pinned_session" in lease_text
    assert "admission_scope(item) == pinned_scope" in lease_text
    assert "source_context_epoch(service) == source_epoch" in lease_text
    assert "current_identity" not in lease_text
    assert "startup_authorized()" in lease_text
    assert "recovery_authorized()" in lease_text
    assert "return False" in lease_text
    assert "if not delivery_admission_current():" in text


def test_provider_checks_admission_during_download():
    path = (Path(__file__).resolve().parents[1]
            / "src/plugins/provider-numilog/cifereca_numilog.py")
    code = path.read_text(encoding="utf-8")
    assert "def download_direct(self,item,target_dir,*,expected_offer_id=None,expected_identity_id=None," in code
    assert "core_admission_current=None" in code
    method_start = code.index("    def download_direct(")
    method_end = code.index("\nclass NumilogPlugin", method_start)
    method = code[method_start:method_end]
    assert "require_core()" in method
    assert "core_admission_current=core_admission_current" in method
    assert "return self._download(" in method


@pytest.mark.parametrize("revocation", [
    "none", "account", "session", "identity", "scope", "startup", "recovery",
    "binding_error", "context_epoch", "ui_selection",
])
def test_numilog_live_lease_refuses_changed_source_or_consent(revocation):
    method = _function("delivery_admission_current")
    item = NS(token="edition")
    service = NS(current_identity=NS(identity_id="alice"),
                 _source_context_epoch=7)
    session = object()
    owner = NS(identity_id="alice")
    account = "borrower"
    scope = ("numilog", "edition")
    delivered = {"owner": owner, "session": session,
                 "account": account, "scope": scope}
    gates = {"startup": True, "recovery": True}

    def require_source(_service, _item, *, session):
        assert session is delivered["session_expected"]
        if revocation == "binding_error":
            raise ValueError("source revoked")
        return delivered["owner"], delivered["session"], delivered["account"]

    delivered["session_expected"] = session
    if revocation == "account":
        delivered["account"] = "another"
    if revocation == "session":
        delivered["session"] = object()
    if revocation == "identity":
        delivered["owner"] = NS(identity_id="bob")
    if revocation == "scope":
        delivered["scope"] = ("numilog", "other")
    if revocation == "startup":
        gates["startup"] = False
    if revocation == "recovery":
        gates["recovery"] = False
    if revocation == "context_epoch":
        service._source_context_epoch = 9
    if revocation == "ui_selection":
        service.current_identity.identity_id = "bob"

    namespace = dict(
        require_source=require_source, service=service, item=item,
        pinned_session=session, pinned_account_id=account,
        identity=owner, admission_scope=lambda _item: delivered["scope"],
        pinned_scope=scope, automatic=True, reconcile_pending=True,
        startup_authorized=lambda: gates["startup"],
        recovery_authorized=lambda: gates["recovery"],
        source_context_epoch=lambda service: service._source_context_epoch,
        source_epoch=7,
    )
    # Pin the original identity, regardless of the later owner mutation.
    namespace["identity"] = NS(identity_id="alice")
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[method], type_ignores=[])), str(SOURCE), "exec"), namespace)
    assert namespace["delivery_admission_current"]() is (
        revocation in {"none", "ui_selection"}
    )
