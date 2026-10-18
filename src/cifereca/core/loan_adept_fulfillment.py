"""One ADEPT execution and binding capture with injected facade dependencies.

The caller retains borrower/profile admission, destination, lock, retry and
ledger ownership. This stage neither publishes nor indexes the artifact.
"""

def fulfill(self, request, *, profile_name, adept_borrower_identity_id,
            trace_ref, time, _TRACE_LOG, trace_integer):
    _TRACE_LOG.info(
        "TRACE download adept begin loan_ref=%s",
        trace_ref,
    )
    adept_started = time.monotonic()
    originating_profile_id = self._originating_adept_profile_id(adept_borrower_identity_id, profile_name)
    result = self.execute_acquisition_request(request)
    from ..adept_binding import capture_binding
    adept_binding = capture_binding(result, profile_name=profile_name, profile_id=originating_profile_id)
    diag = self.runtime_diagnostic("runtime.kobo-adept")
    _TRACE_LOG.info(
        "TRACE download adept end loan_ref=%s returncode=%s duration_ms=%s dt=%.3fs",
        trace_ref, trace_integer(diag.get("returncode"), minimum=-255, maximum=255),
        trace_integer(diag.get("duration_ms"), minimum=0, maximum=86_400_000), time.monotonic()-adept_started,
    )
    return result, adept_binding
