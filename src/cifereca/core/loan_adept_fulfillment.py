"""One ADEPT execution and binding capture with injected facade dependencies.

The caller retains borrower/profile admission, destination, lock, retry and
ledger ownership. This stage neither publishes nor indexes the artifact.
"""

class SourceEpochRevoked(RuntimeError):
    """The pinned source changed before ADEPT execution; no provider detail."""


def fulfill(self, request, *, profile_name, adept_borrower_identity_id,
            trace_ref, time, _TRACE_LOG, trace_integer,
            source_epoch_current=None):
    _TRACE_LOG.info(
        "TRACE download adept begin loan_ref=%s",
        trace_ref,
    )
    adept_started = time.monotonic()
    originating_profile_id = self._originating_adept_profile_id(adept_borrower_identity_id, profile_name)
    # The originating-profile lookup is another asynchronous-capable hook.
    # A previously valid source may have been rebound A->B->A during it.
    # Check the pinned SQLite generation *after* this hook and immediately
    # before the one-shot ADEPT execution; never refresh the grant here.
    if source_epoch_current is not None:
        try:
            valid = source_epoch_current() is True
        except Exception:
            valid = False
        if not valid:
            raise SourceEpochRevoked()
    result = self.execute_acquisition_request(request)
    from ..adept_binding import capture_binding
    adept_binding = capture_binding(result, profile_name=profile_name, profile_id=originating_profile_id)
    # Keep the historical exception contract of runtime_diagnostic(): an
    # explicit runtime failure still propagates unchanged to the caller.
    # Only projection of unexpected return *values* is fail-closed, without
    # invoking an untrusted mapping subclass's getter.
    diag = self.runtime_diagnostic("runtime.kobo-adept")

    def runtime_fact(name):
        return diag.get(name) if type(diag) is dict else None

    _TRACE_LOG.info(
        "TRACE download adept end loan_ref=%s returncode=%s duration_ms=%s dt=%.3fs",
        trace_ref, trace_integer(runtime_fact("returncode"), minimum=-255, maximum=255),
        trace_integer(runtime_fact("duration_ms"), minimum=0, maximum=86_400_000), time.monotonic()-adept_started,
    )
    return result, adept_binding
