"""Read-only historical origin evidence, independent of new-acquisition grants."""
from collections.abc import Mapping


def inventory_attests_return_origin(binding: Mapping) -> bool:
    """Recorded inventory evidence permits routing to the historical profile.

    This does not prove fresh server state or authorize replay. The explicit
    return still resolves the origin profile and reads its exact loan/token
    before mutation, then verifies the same inventory after mutation.
    """
    if not isinstance(binding, Mapping):
        return False
    state = binding.get("loan_state")
    consistent = (
        (state == "active" and binding.get("expired") is False
         and binding.get("token_present") is True
         and binding.get("return_verification") == "active_confirmed")
        or (state == "returned" and binding.get("token_present") is False
            and binding.get("return_verification") == "confirmed")
        or (state == "expired" and binding.get("expired") is True
            and binding.get("return_verification") == "active_confirmed")
    )
    return bool(
        consistent
        and binding.get('inventory_status') == 'resolved'
        and binding.get('binding_source') in ('public_inventory', 'manual_selection')
        and type(binding.get('inventory_checked_at')) is int
        and binding['inventory_checked_at'] > 0
        and all(isinstance(binding.get(k), str) and binding[k].strip()
                for k in ('adept_profile_id', 'adept_profile_name', 'adept_loan_id'))
        and binding.get('loan_state') in ('active', 'expired', 'returned')
        and type(binding.get('token_present')) is bool
        and type(binding.get('expired')) is bool
        and binding.get('return_verification') in ('active_confirmed', 'confirmed')
    )
