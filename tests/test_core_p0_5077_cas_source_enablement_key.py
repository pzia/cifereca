"""#5077: CAS source consent does not import the legacy consent decision bridge."""
import ast
import inspect

import pytest

from cifereca.core import source_sync_consent as cas
from cifereca.core.legacy_source_identifier import legacy_source_id
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


@pytest.mark.parametrize("provider,account", [
    ("fixture-a", "primary"),
    ("fixture-b", "borrower-2"),
    ("fixture-b", ""),
    ("fixture-b", "fixture-b:card:primary"),
])
def test_inventory_enablement_key_is_stable_and_never_an_acquisition_grant(
    provider, account,
):
    scope = ProviderRightsScope("alice", provider, "card", account)
    expected = "sync.source.enabled." + legacy_source_id(
        "alice", provider, "card", account,
    )
    assert cas.source_enablement_key(scope) == expected
    # Legacy source enablement does NOT materialize the new CAS opt-in.
    class Storage:
        def metadata_get(self, key):
            return "1" if key == expected else None

    policy = cas.read_policy(Storage(), scope)
    assert policy.mode == "unset" and policy.revision is None


def test_cas_decision_code_never_imports_old_auto_consent_bridge():
    tree = ast.parse(inspect.getsource(cas))
    bridge_imports = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "source_auto_consent_bridge"
    ]
    assert bridge_imports == []
