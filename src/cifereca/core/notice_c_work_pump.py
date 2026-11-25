"""#4982: explicit Core-only pump for known public aggregate implementations.

No timer, thread, extra queue, automatic caller or production activation.
An existing Core maintenance coordinator may drive tick outside GTK,
including on an admitted maintenance worker that never waits for submitted
work. The existing broker owns all actual work.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
import time

from . import notice_c_job_journal as journal
from . import notice_c_exact_recompute as exact
from . import notice_c_initial_publication as initial
from . import notice_c_initial_group_publication as initial_group
from . import notice_c_origin_recompute as origin
from . import notice_c_origin_relink as relink
from .notice_c_work_bridge import AggregateWorkBridge, PreparedAggregate
from .notice_c_reparse_execution import PublicReplayRegistry, prepare_public_reparse
from .notice_c_fetch_execution import PublicFetchExecution
from .notice_c_purchase_execution import PurchaseIntentExecution
from .notice_c_dispatch_rotation import PublicDispatchRotation, RotationPage, _PURCHASE_LANE

_SUPPORTED = (
    ("recompute_aggregate", exact.IMPLEMENTATION_DIGEST),
    ("recompute_aggregate", initial.IMPLEMENTATION_DIGEST),
    ("recompute_aggregate", origin.IMPLEMENTATION_DIGEST),
    ("relink_notices", relink.IMPLEMENTATION_DIGEST),
    ("relink_notices", initial_group.IMPLEMENTATION_DIGEST),
)


@dataclass(frozen=True, slots=True, repr=False)
class DispatchTick:
    handles: tuple
    examined: int
    corrupt: int
    deferred: bool
    busy: bool = False


class PublicNoticeWorkPump:
    """Bounded round-robin scan of enabled public work classes.

    Unsupported implementations never enter the broker. Backpressure preserves
    the first unsubmitted position, without retaining an in-memory job queue.
    Fairness is by enabled work class and cyclic key, not by provider/account.
    The journal remains the sole pending store.
    One instance per Core owner; admission/fencing govern actual execution.
    """

    def __init__(self, storage, broker, *, clock=None, on_committed=None,
                 replay_registry=None, fetch_execution=None, purchase_execution=None,
                 on_purchase_committed=None, on_witness_rejected=None,
                 capture_directory=None, prepare_public_age=None, public_age_digests=(),
                 on_descriptive_committed=None):
        if clock is not None and not callable(clock):
            raise ValueError("notice_work_invalid_clock")
        if replay_registry is None:
            replay_registry = PublicReplayRegistry()
        if type(replay_registry) is not PublicReplayRegistry:
            raise ValueError("notice_reparse_invalid_registry")
        if fetch_execution is not None and (
                type(fetch_execution) is not PublicFetchExecution
                or fetch_execution._authority._storage is not storage):
            raise ValueError("notice_fetch_invalid_registry")
        if purchase_execution is not None and (
                type(purchase_execution) is not PurchaseIntentExecution
                or purchase_execution._storage is not storage):
            raise ValueError("notice_purchase_invalid_registry")
        if (type(public_age_digests) is not tuple or len(public_age_digests) > 128
                or any(not journal._digest(value) for value in public_age_digests)
                or len(set(public_age_digests)) != len(public_age_digests)
                or (prepare_public_age is None) != (not public_age_digests)
                or (prepare_public_age is not None and not callable(prepare_public_age))):
            raise ValueError("notice_work_invalid_public_age_port")
        self._prepare_public_age = prepare_public_age
        self._public_age_digests = public_age_digests
        self._purchase_execution = purchase_execution
        self._fetch_execution = fetch_execution
        self._replay_registry = replay_registry
        self._supported = (_SUPPORTED + replay_registry.supported()
                           + (() if fetch_execution is None else fetch_execution.supported()))
        self._capture_directory = capture_directory
        self._storage = storage
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._bridge = AggregateWorkBridge(
            storage, broker, self._prepare, clock=self._clock,
            on_committed=on_committed, allow_reparse=bool(replay_registry.supported()),
            allow_relink=True,
            prepare_fetch=(self._prepare_fetch if fetch_execution is not None
                           and fetch_execution.supported() else None),
            on_purchase_committed=on_purchase_committed,
            on_witness_rejected=on_witness_rejected,
            claim_purchase=None if purchase_execution is None else purchase_execution.claim,
            prepare_purchase=None if purchase_execution is None else purchase_execution.prepare,
            finish_purchase=None if purchase_execution is None else purchase_execution.finish,
            prepare_descriptive=self._prepare_age if prepare_public_age is not None else None,
            on_descriptive_committed=on_descriptive_committed,
        )
        lanes = tuple(dict.fromkeys(kind for kind, _digest in self._supported))
        if purchase_execution is not None:
            lanes += (_PURCHASE_LANE,)
        if prepare_public_age is not None:
            lanes += ("consolidate_descriptive",)
        self._rotation = PublicDispatchRotation(lanes, public_age=prepare_public_age is not None)
        self._tick_lock = Lock()

    def _prepare_age(self, spec, control):
        from .notice_c_work_bridge import UnavailableNoticeWork
        if (spec.kind != "consolidate_descriptive" or spec.public_age_locator is None
                or spec.implementation_digest not in self._public_age_digests):
            raise UnavailableNoticeWork("notice_descriptive_unsupported_spec")
        return self._prepare_public_age(spec, control)

    def _prepare_fetch(self, spec, lease, control):
        return self._fetch_execution.prepare(
            self._storage, spec, lease, control, clock=self._clock,
            capture_directory=self._capture_directory,
        )

    def _prepare(self, spec, control):
        control.check_cancelled()
        if spec.kind == "reparse_cached_input":
            return prepare_public_reparse(
                self._storage, self._replay_registry, spec, control, clock=self._clock,
                capture_directory=self._capture_directory,
            )
        if spec.kind == "relink_notices":
            if spec.implementation_digest == initial_group.IMPLEMENTATION_DIGEST:
                publish = initial_group.prepare_initial_group_publication(self._storage, spec)
            else:
                publish = relink.prepare_origin_public_relink(self._storage, spec)
            control.check_cancelled()
            return PreparedAggregate(publish)
        if spec.kind != "recompute_aggregate":
            raise ValueError("notice_recompute_unsupported_spec")
        if spec.implementation_digest == exact.IMPLEMENTATION_DIGEST:
            publish = exact.prepare_exact_public_recompute(self._storage, spec)
        elif spec.implementation_digest == initial.IMPLEMENTATION_DIGEST:
            publish = initial.prepare_initial_publication(self._storage, spec)
        elif spec.implementation_digest == origin.IMPLEMENTATION_DIGEST:
            publish = origin.prepare_origin_public_recompute(self._storage, spec)
        else:
            raise ValueError("notice_recompute_unsupported_spec")
        control.check_cancelled()
        return PreparedAggregate(publish)

    def tick(self, *, control=None):
        """Scan bounded class slices and submit hints without waiting.

        Reentry is rejected immediately. A refusal leaves the cursor at
        the last accepted key (or original position), so a saturated
        broker cannot silently drop later eligible work from this sweep.
        """
        if not self._tick_lock.acquire(blocking=False):
            return DispatchTick((), 0, 0, False, True)
        try:
            if control is not None:
                control.check_cancelled()
            now = self._clock()

            def read(kind, after, limit):
                if kind == _PURCHASE_LANE:
                    # Independent exact-key lane, within the SAME rotation's
                    # 32-row / two-submission tick budget. Never sweep inert
                    # private intents or wait for sparse public fetch keys.
                    refs = self._purchase_execution.ready_page_refs(after=after, limit=limit)
                    supported = (("fetch_source_notice", journal._PURCHASE_INTENT_DIGEST),)
                    options = {"purchase_refs": refs}
                elif kind == "consolidate_descriptive":
                    supported = None
                    options = {"public_age_digests": self._public_age_digests}
                else:
                    supported = tuple(pair for pair in self._supported if pair[0] == kind)
                    options = {}
                page = journal.read_ready_page(
                    self._storage, now=now, after=after, limit=limit,
                    supported=supported, **options,
                )
                return RotationPage(page.refs, page.next_after, page.examined, page.corrupt)

            def offer_lane(kind, ref):
                circuit = "network" if kind in ("fetch_source_notice", _PURCHASE_LANE) else "local"
                return self._bridge.offer(ref, circuit=circuit)

            tick = self._rotation.tick(
                read, self._bridge.offer, offer_lane=offer_lane,
                check_cancelled=control.check_cancelled if control is not None else None,
            )
            return DispatchTick(tick.handles, tick.examined, tick.corrupt, tick.deferred)
        finally:
            self._tick_lock.release()
