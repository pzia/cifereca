"""Reconcile local acquisition provenance against the public ADEPT inventory.

No fulfillment, profile creation, return command or local file mutation occurs
here. Preserve the facade's existing cache, profile candidate ordering, exact
recorded loan ID precedence, ambiguity rules and ledger writes. The facade
imports the path matcher at call time and injects exception/clock seams.
"""


def _return_capability_for_batch(service, runtime, cache):
    """Reuse only a positive flag in this read-only reconciliation batch.

    This is presentation/readiness, never permission to return a loan. The
    explicit return path independently reads fresh capabilities and registry.
    Failures/negative results are not cached, and no cache crosses Sync calls.
    """
    try:
        same_runtime = service._kobo_adapter() is runtime
    except Exception:
        same_runtime = False
    prior = cache.get("return_capability")
    if same_runtime and prior is not None and prior[0] is runtime:
        return True
    available = service._adept_return_capability()
    if available is True and same_runtime:
        # Do not label a replacement runtime's capability as this instance's.
        try:
            if service._kobo_adapter() is runtime:
                cache["return_capability"] = (runtime, True)
        except Exception:
            pass
    return available


def reconcile_adept_acquisition(
    self, row, *, profile_references=None, cache=None,
    match_local_book, AttentionRequired, time,
):
    metadata = dict(row.get("metadata") or {})
    binding = dict(metadata.get("adept_binding") or {})
    path = self._adept_acquisition_path(row)
    if not path or not path.is_file():
        return {"status": "artifact_missing", "acquisition_id": row["acquisition_id"]}
    runtime = self._kobo_adapter()
    inventory_fn = getattr(runtime, "loan_inventories", None)
    if not callable(inventory_fn):
        raise AttentionRequired("Inventaire indisponible : runtime Kobo-Adept 1.2.2 requis.", code="adept_inventory_unavailable")
    cache = cache if cache is not None else {}
    if "profiles" not in cache:
        cache["profiles"] = self.kobo_profiles()
    profiles = cache["profiles"]
    candidates = self._adept_inventory_candidates(row, profiles, profile_references)
    if not candidates:
        return {"status": "profile_missing", "acquisition_id": row["acquisition_id"]}
    names = tuple(self._adept_profile_name(p) for p in candidates)
    if names not in cache:
        try:
            cache[names] = inventory_fn(list(names))
        except Exception:
            binding.update(inventory_status="inventory_error", return_available=False, return_verification="verification_failed")
            metadata["adept_binding"] = binding
            self.storage.acquisition_put({**row, "metadata": metadata})
            raise
    inventory = cache[names]
    matches = match_local_book(path, inventory)
    if binding.get("adept_loan_id"):
        exact = [{"profile": group["profile"], "loan": loan} for group in inventory.get("profiles", [])
                 for loan in group.get("loans", []) if loan["id"] == binding["adept_loan_id"]]
        if exact:
            matches = exact
    # A partial inventory cannot establish a globally unique binding.
    status = "inventory_error" if not inventory.get("complete") else ("unresolved" if not matches else ("ambiguous" if len(matches) != 1 else "resolved"))
    if status == "resolved":
        match = matches[0]; loan = match["loan"]
        profile = next(p for p in candidates if self._adept_profile_name(p) == match["profile"])
        # A prior authoritative ID is never replaced by a different candidate.
        if binding.get("adept_loan_id") and binding["adept_loan_id"] != loan["id"]:
            status = "binding_conflict"
        else:
            returned = loan["returned"]
            state = "returned" if returned else ("expired" if loan["expired"] else "active")
            binding.update(adept_profile_id=self._adept_profile_ref(profile), adept_profile_name=match["profile"],
                adept_loan_id=loan["id"], loan_state=state, token_present=loan["token_present"],
                expired=loan["expired"], validity=loan.get("validity"), inventory_status="resolved",
                inventory_checked_at=int(time.time()), return_verification=("verification_failed" if loan["token_present"] else "confirmed") if returned else "active_confirmed", binding_source=binding.get("binding_source") if binding.get("binding_source") == "manual_selection" else "public_inventory",
                return_available=bool(not returned and not loan["expired"] and loan["token_present"] and _return_capability_for_batch(self, runtime, cache)))
    if status != "resolved":
        # Keep historical IDs and files, record absence/ambiguity separately.
        binding.update(inventory_status=status, inventory_checked_at=int(time.time()), return_available=False, return_verification="verification_failed")
    metadata["adept_binding"] = binding
    self.storage.acquisition_put({**row, "metadata": metadata})
    self.diagnostics.add("ADEPT", "inventory reconciliation", "ok" if status == "resolved" else "warning",
                         detail={"status": status, "profiles": len(names), "matches": len(matches)}, level="normal")
    return {"status": status, "acquisition_id": row["acquisition_id"], "matches": len(matches), "return_available": binding.get("return_available", False)}

