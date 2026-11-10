"""P0 #5054: an unknown provider must not be invisible to Numilog resume."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


SOURCE = (Path(__file__).resolve().parents[1] /
          "src/cifereca/core/numilog_materialization_checkpoint.py")


def _selector():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "target_for_entry")
    def _require(value):
        if not value:
            raise ValueError("ambiguous or incomplete")
    namespace = {"_require": _require}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[node], type_ignores=[])), str(SOURCE), "exec"), namespace)
    return namespace["target_for_entry"]


class _NoStorage:
    def __getattr__(self, key):
        raise AssertionError("ambiguous rights must not access SQLite: " + key)


def _right(provider, *, identity="alice", kind="owned", state="active"):
    return {"identity_id": identity, "provider_id": provider,
            "kind": kind, "state": state, "item_id": "one",
            "entitlement_id": "owned-one"}


@pytest.mark.parametrize("provider_id", [
    "unknown", "synthetic-alpha", "synthetic-beta",
    "bnf-pret", "paris", None, "",
])
@pytest.mark.parametrize("order", [False, True])
def test_unknown_competitor_blocks_before_receipt_or_provider_io(provider_id, order):
    rights = [_right("numilog"), _right(provider_id)]
    if order:
        rights.reverse()
    service = NS(current_identity=NS(identity_id="alice"), storage=_NoStorage())
    assert _selector()(service, {"possessions": rights}) is None


@pytest.mark.parametrize("foreign", [
    _right("synthetic-alpha", identity="other"),
    _right("synthetic-beta", kind="loan"),
    _right("synthetic-beta", state="expired"),
])
def test_non_competing_right_is_excluded_from_active_owned_ambiguity(foreign):
    # Confirm by substituting a passive storage probe. The surviving
    # Numilog candidate reaches the ordinary entitlement/receipt lookup,
    # rather than being rejected due to a non-competing right.
    calls = []
    class Probe:
        def numilog_possession_rights(self, **kwargs):
            calls.append(kwargs)
            return []
    service = NS(current_identity=NS(identity_id="alice"), storage=Probe())
    assert _selector()(service, {"possessions": [_right("numilog"), foreign]}) is None
    assert len(calls) == 1
