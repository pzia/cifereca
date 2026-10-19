"""Explicit ADEPT return orchestration, using the recorded origin only.

The LibraryService facade owns the inter-process lock and injects its historical
exception/request/clock seams. Inventory is refreshed before mutation and again
before success; persistence, retry decisions and artifact preservation retain
their original order. This module never selects a new profile or acquires ACSM.
"""
from typing import Any


def return_adept_acquisition(
    self, acquisition_id: str, *, AttentionRequired, ProviderConfigurationError,
    DrmMutationRequest, time,
) -> dict[str, Any]:
    """Explicit return, exact origin/ID, fresh registry before and after CLI."""
    row = self.storage.acquisition_get(acquisition_id)
    if not row:
        raise AttentionRequired("Acquisition inconnue.", code="adept_acquisition_unknown")
    metadata = dict(row.get("metadata") or {})
    binding = dict(metadata.get("adept_binding") or {})
    if not binding.get("adept_loan_id") or not binding.get("adept_profile_id"):
        raise AttentionRequired("Liaison ADEPT et profil d’origine requis.", code="adept_return_binding_required")
    profile_row = self._adept_profile_row(binding["adept_profile_id"], self.kobo_profiles())
    if not profile_row:
        raise AttentionRequired("Le profil ADEPT d’origine est indisponible.", code="adept_origin_profile_missing")
    profile_name = self._adept_profile_name(profile_row)
    runtime = self._kobo_adapter()
    def persist():
        metadata["adept_binding"] = dict(binding)
        self.storage.acquisition_put({**row, "metadata": dict(metadata)})
    def read_exact():
        try:
            inventory = runtime.loans(profile_name)
            matches = [r for r in inventory["loans"] if r["id"] == binding["adept_loan_id"]]
            if len(matches) != 1 or any(type(matches[0].get(k)) is not bool for k in ("returned", "expired", "token_present")):
                raise ValueError("loan missing or ambiguous")
            return matches[0]
        except Exception:
            # ADEPT registry failures can include original profile paths,
            # signed fulfillment URLs, accounts, stderr or a hostile __str__.
            # A constant UserFacingError is not sufficient if traceback
            # formatting prints its chained provider exception.
            raise AttentionRequired(
                "État ADEPT non confirmé. Vérifiez le prêt ; le fichier est conservé.",
                code="adept_return_verification_required",
            ) from None
    try:
        fresh = read_exact()
    except Exception:
        binding.update(return_available=False, return_verification="verification_failed")
        persist(); raise
    binding.update(loan_state="returned" if fresh["returned"] else ("expired" if fresh["expired"] else "active"),
                   token_present=fresh["token_present"], expired=fresh["expired"],
                   inventory_status="resolved", inventory_checked_at=int(time.time()),
                   return_verification="confirmed" if fresh["returned"] else "active_confirmed")
    if fresh["returned"] and fresh["token_present"]:
        binding.update(return_available=False, return_verification="verification_failed")
        persist()
        self.diagnostics.add("ADEPT", "return verification", "warning", detail={"reason":"returned_with_token", "mutation":"skipped"}, level="normal")
        raise AttentionRequired("Registre marqué restitué, mais jeton ADEPT toujours présent. Restitution non confirmée ; vérifiez avec libgourou. Fichier conservé.", code="adept_return_token_conflict")
    if fresh["returned"]:
        binding["return_available"] = False; persist()
        return {"ok": True, "already_returned": True, "verification": "confirmed", "artifact_preserved": True}
    binding.pop("returned_at", None)
    binding["return_available"] = bool(not fresh["expired"] and fresh["token_present"] and self._adept_return_capability())
    persist()
    if not binding["return_available"]:
        raise AttentionRequired("Restitution indisponible pour l’état ADEPT courant.", code="adept_return_state_required")
    request = DrmMutationRequest(operation="return", identity_id=str(row.get("identity_id") or ""),
        provider_id=row["provider_id"], entitlement_id=str(row.get("entitlement_id") or ""),
        metadata={"adept_loan_id": binding["adept_loan_id"], "adept_profile_name": profile_name})
    binding.update(return_verification="pending", return_available=False); persist()
    try:
        result = self.execute_drm_mutation(request)
        returned = dict(result.get("metadata") or {}).get("runtime_result") or {}
        if (result.get("ok") is not True or returned.get("ok") is not True
                or returned.get("operation") != "loan_return"
                or returned.get("loan_id") != binding["adept_loan_id"] or returned.get("profile") != profile_name):
            raise ProviderConfigurationError("Restitution ADEPT non confirmée")
        after = read_exact()
        if not after["returned"] or after["token_present"]:
            raise AttentionRequired("Commande exécutée, mais restitution non confirmée dans ADEPT. Vérifiez le prêt ; fichiers conservés.", code="adept_return_verification_required")
    except Exception:
        binding.update(return_verification="verification_failed", return_available=False)
        persist(); raise
    binding.update(loan_state="returned", token_present=False, return_available=False, returned_at=int(time.time()), return_verification="confirmed")
    persist()
    return {"ok": True, "operation": "loan_return", "artifact_preserved": True, "verification": "confirmed"}

