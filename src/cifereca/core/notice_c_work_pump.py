"""#4982: explicit Core-only pump for known public aggregate implementations.

No timer, thread, extra queue, automatic caller or production activation.
An existing Core maintenance coordinator may drive tick outside GTK and
outside the broker's worker pool. The existing broker owns all actual work.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
import time

from . import notice_c_job_journal as journal
from . import notice_c_exact_recompute as exact
from . import notice_c_origin_recompute as origin
from .notice_c_work_bridge import AggregateWorkBridge, PreparedAggregate

_SUPPORTED = (
    ("recompute_aggregate", exact.IMPLEMENTATION_DIGEST),
    ("recompute_aggregate", origin.IMPLEMENTATION_DIGEST),
)


@dataclass(frozen=True, slots=True, repr=False)
class DispatchTick:
    handles: tuple
    examined: int
    corrupt: int
    deferred: bool
    busy: bool = False


class PublicNoticeWorkPump:
    """One bounded page per explicit tick, fair cyclic public logical keys.

    Unsupported implementations never enter the broker. Backpressure preserves
    the first unsubmitted position, without retaining an in-memory job queue.
    This is fairness among eligible opaque public keys, NOT per-account/source
    or priority-class fairness. The journal remains the sole pending store.
    One instance per Core owner; admission/fencing govern actual execution.
    """

    def __init__(self, storage, broker, *, clock=None, on_committed=None):
        if clock is not None and not callable(clock):
            raise ValueError("notice_work_invalid_clock")
        self._storage = storage
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._bridge = AggregateWorkBridge(
            storage, broker, self._prepare, clock=self._clock,
            on_committed=on_committed,
        )
        self._after = None
        self._tick_lock = Lock()

    def _prepare(self, spec, control):
        control.check_cancelled()
        if spec.kind != "recompute_aggregate":
            raise ValueError("notice_recompute_unsupported_spec")
        if spec.implementation_digest == exact.IMPLEMENTATION_DIGEST:
            publish = exact.prepare_exact_public_recompute(self._storage, spec)
        elif spec.implementation_digest == origin.IMPLEMENTATION_DIGEST:
            publish = origin.prepare_origin_public_recompute(self._storage, spec)
        else:
            raise ValueError("notice_recompute_unsupported_spec")
        control.check_cancelled()
        return PreparedAggregate(publish)

    def tick(self):
        """Scan once and submit hints without waiting for execution.

        Reentry is rejected immediately. A refusal leaves the cursor at
        the last accepted key (or original position), so a saturated
        broker cannot silently drop later eligible work from this sweep.
        """
        if not self._tick_lock.acquire(blocking=False):
            return DispatchTick((), 0, 0, False, True)
        try:
            page = journal.read_ready_page(
                self._storage, now=self._clock(), after=self._after,
                supported=_SUPPORTED,
            )
            handles = []
            for ref in page.refs:
                handle = self._bridge.submit(ref)
                if handle is None:
                    return DispatchTick(tuple(handles), page.examined,
                                        page.corrupt, True)
                handles.append(handle)
                self._after = ref.key
            self._after = page.next_after
            return DispatchTick(tuple(handles), page.examined, page.corrupt, False)
        finally:
            self._tick_lock.release()
