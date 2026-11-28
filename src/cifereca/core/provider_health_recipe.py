"""Explicit sequential health recipe using already admitted inventory readers.

No synchronization, account connection, credential creation, acquisition,
delivery or fallback. Unsupported steps are reported as unknown. Only a
currently selected identity with an exact registered account can execute.
"""
from inspect import getattr_static

from ..models import Account
from ..plugin_api.capture_vault import CaptureScope
from ..plugin_api.loan_inventory import read_loan_inventory
from ..diagnostic_task_context import diagnostic_task_scope
from ..http_trace import HTTP_TRACE
from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from .provider_health_inventory import _diagnostic_generation
from .provider_health_search import begin_account_read, finish_account_read, loan_inventory_outcome, rights_outcome
from .rights_snapshot_read import read_account_rights_snapshot
from .private_inventory_binding import private_inventory_binding_current
from .loan_action_scope import _inert_session_state, _inert_session_accounts
from .capture_vault_service import capture_scope_authorizer

_STEPS = ("search", "detail", "pagination", "availability", "personal_library",
          "loans", "quotas", "rights")


def _scope(account):
    return CaptureScope(account.provider_id, account.credential_id,
                        account.unique_id, account.identity_id)


def _identity(service):
    return getattr(getattr(service, "current_identity", None), "identity_id", None)


def recipe_options(service):
    """Metadata-only list; no provider getter or live connection is opened."""
    try:
        health = service._provider_health
        generation = _diagnostic_generation(service, health)
        identity = _identity(service)
        if generation is None or not identity:
            return ()
        authorize = capture_scope_authorizer(service)
        result = []
        for account in service.accounts_for_identity(identity):
            if type(account) is not Account:
                continue
            scope = _scope(account)
            if (not all(type(v) is str and v for v in (
                    scope.provider_id, scope.instance_id, scope.account_id, scope.identity_id))
                    or scope.identity_id != identity
                    or not authorize("ui.page.diagnostics", scope)):
                continue
            result.append({"scope": scope, "generation": generation,
                           "enabled": service._provider_enabled_for_identity(
                               scope.provider_id, identity) is True})
        if _identity(service) != identity or _diagnostic_generation(service, health) != generation:
            return ()
        return tuple(result[:256])
    except Exception:
        return ()


def run_recipe(service, scope, generation, *, confirmed=False, control=None):
    """One explicit account; all steps reported, unsupported never success."""
    def report(status, reason):
        return tuple({"capability": name, "outcome": status, "reason": reason}
                     for name in _STEPS)

    if confirmed is not True:
        return report("unknown", "confirmation_required")
    if type(scope) is not CaptureScope or type(generation) is not tuple:
        return report("unknown", "scope_denied")

    try:
        health = service._provider_health
        if not any(row["scope"] == scope and row["generation"] == generation
                   and row["enabled"] for row in recipe_options(service)):
            return report("unknown", "scope_denied")
        matches = [row for row in service.accounts
                   if type(row) is Account and _scope(row) == scope]
        if len(matches) != 1:
            return report("unknown", "scope_denied")
        account = matches[0]
        session = service._sessions.get(service._session_key(scope.provider_id, scope.instance_id))
        state = _inert_session_state(session)
        upstream = _inert_session_accounts(session)
        if state is None or upstream is None or len(upstream) != 1 or upstream[0] is not account:
            return report("unknown", "unqualified_session")
        descriptor = state.get("descriptor")
        session_epoch = state.get("_epoch")
        coordinates = (scope.identity_id, scope.provider_id, scope.instance_id, scope.account_id)

        def current():
            try:
                current_state = _inert_session_state(session)
                return (not (control is not None and control.cancelled())
                        and _identity(service) == scope.identity_id
                        and service._provider_health is health
                        and _diagnostic_generation(service, health) == generation
                        and HTTP_TRACE.status()["active"] is True
                        and current_state is not None
                        and current_state.get("_epoch") == session_epoch
                        and current_state.get("descriptor") is descriptor
                        and private_inventory_binding_current(service, account, session,
                                                             scope=coordinates, epoch=generation[0]))
            except Exception:
                return False

        results = {name: {"capability": name, "outcome": "unknown",
                          "reason": "reader_not_qualified"} for name in _STEPS}
        def put(name, outcome, reason):
            results[name] = {"capability": name, "outcome": outcome or "unknown", "reason": reason}

        with InterProcessLock(acquisition_lock_file()):
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # Deliberately typed-only: a list-only legacy fallback does not
            # establish coverage and is not admitted for this explicit recipe.
            if (getattr(getattr(descriptor, "capabilities", None), "loans", None) is True
                    and callable(getattr_static(session, "loan_inventory_snapshot", None))):
                with diagnostic_task_scope():
                    call = begin_account_read(service, session, account, "loans")
                    if call is not None and current():
                        snapshot = read_loan_inventory(session, account, descriptor=descriptor,
                                                       scope_current=current, typed_only=True)
                        if not current():
                            return report("unknown", "scope_or_trace_unavailable")
                        outcome = loan_inventory_outcome(snapshot)
                        finish_account_read(service, session, account, call, outcome)
                        put("loans", outcome, "typed_inventory")
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            if any(callable(getattr_static(session, name, None)) for name in (
                    "downloadable_resource_snapshot", "provider_rights_snapshot")):
                snapshot = read_account_rights_snapshot(service, account, cancelled=lambda: not current())
                if not current():
                    return report("unknown", "scope_or_trace_unavailable")
                put("rights", rights_outcome(snapshot), "typed_rights")
            return tuple(results[name] for name in _STEPS)
    except LockBusyError:
        return report("unknown", "busy")
    except Exception:
        return report("unknown", "recipe_unavailable")
