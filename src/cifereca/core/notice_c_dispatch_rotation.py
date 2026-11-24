"""#4982 bounded public class rotation policy (not a dispatcher or queue).

The future pump owns synchronization, journal page reads and submissions to
its existing broker. This policy owns only bounded opaque lane cursors and a rotor.
Pages and accepted handles are tick-local; no pending work is retained.
There is no Storage write, claim, timer, transport, pool or private scope.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import notice_c_job_journal as journal

_PURCHASE_LANE = "purchase_attested"
_ORDER = tuple(kind for kind in journal._KINDS if kind != "consolidate_descriptive") + (_PURCHASE_LANE,)
_SCAN_BUDGET = 32
_SUBMIT_BUDGET = 2


@dataclass(frozen=True, slots=True, repr=False)
class RotationPage:
    refs: tuple[journal.JobRef, ...]
    next_after: str | None
    examined: int
    corrupt: int


@dataclass(frozen=True, slots=True, repr=False)
class RotationOffer:
    outcome: str  # accepted | duplicate | blocked | circuit_blocked
    handle: object = None


@dataclass(frozen=True, slots=True, repr=False)
class RotationTick:
    handles: tuple
    examined: int
    corrupt: int
    duplicates: int
    deferred: bool


class PublicDispatchRotation:
    """Round-robin by enabled work class, cyclic opaque key order within class.

    Under continued ticks and available background slots, a continuously
    eligible key is revisited within a finite sweep of the bounded journal.
    A permanently saturated broker has no liveness guarantee. This is NOT
    per-provider/owner fairness, deadline ordering or private-account
    isolation. One caller must serialize tick (reuse the pump's lock).

    read_page(kind, after, limit) returns a snapshot of ready supported
    refs of that kind; scanned rows, not just matches, count against limit.
    offer(ref) distinguishes already locally submitted exact generation
    from global/local capacity refusal. Only an actual accepted broker
    handle counts toward the two-submission budget. The worker still
    rechecks the exact durable generation, lease and authority.
    """
    def __init__(self, kinds, *, public_age=False):
        if type(public_age) is not bool:
            raise ValueError("notice_rotation_invalid_kinds")
        order = tuple(journal._KINDS) + (_PURCHASE_LANE,) if public_age else _ORDER
        if (type(kinds) is not tuple or not kinds
                or any(type(kind) is not str or kind not in order for kind in kinds)
                or len(set(kinds)) != len(kinds)):
            raise ValueError("notice_rotation_invalid_kinds")
        self._kinds = tuple(kind for kind in order if kind in kinds)
        self._after = dict.fromkeys(self._kinds)
        self._next = 0

    def _page(self, read_page, kind, limit):
        after = self._after[kind]
        page = read_page(kind, after, limit)
        if (type(page) is not RotationPage
                or type(page.refs) is not tuple
                or type(page.examined) is not int or not 0 <= page.examined <= limit
                or type(page.corrupt) is not int or not 0 <= page.corrupt <= page.examined
                or len(page.refs) + page.corrupt > page.examined
                or (page.next_after is not None and (
                    not journal._digest(page.next_after)
                    or page.examined != limit
                    or (after is not None and page.next_after <= after)))):
            raise ValueError("notice_rotation_invalid_page")
        for ref in page.refs:
            journal._check_ref(ref)
        keys = tuple(ref.key for ref in page.refs)
        if (keys != tuple(sorted(set(keys)))
                or (keys and after is not None and keys[0] <= after)
                or (keys and page.next_after is not None and keys[-1] > page.next_after)):
            raise ValueError("notice_rotation_invalid_page")
        return page

    def tick(self, read_page, offer, *, check_cancelled=None, offer_lane=None):
        if (not callable(read_page) or not callable(offer)
                or (check_cancelled is not None and not callable(check_cancelled))
                or (offer_lane is not None and not callable(offer_lane))):
            raise ValueError("notice_rotation_invalid_port")
        check = check_cancelled if check_cancelled is not None else lambda: None
        count = len(self._kinds)
        start = self._next
        order = tuple((start+i) % count for i in range(count))
        quotient, remainder = divmod(_SCAN_BUDGET, count)
        pages = {}
        positions = {}
        examined = corrupt = duplicates = 0
        handles = []
        blocked_lanes = set()

        # Every enabled class receives a scan slice, including a sparse
        # class. Empty pages advance even when another class is saturated.
        for offset, index in enumerate(order):
            check()
            kind = self._kinds[index]
            page = self._page(read_page, kind, quotient + int(offset < remainder))
            pages[index] = page
            positions[index] = 0
            examined += page.examined
            corrupt += page.corrupt
            if not page.refs:
                self._after[kind] = page.next_after

        # Visit one candidate per class in each round. If a candidate is
        # blocked, preserve its cursor and make its lane first next tick.
        # A duplicate is safe to skip for this sweep, not a capacity signal.
        while True:
            more = False
            for index in order:
                page = pages[index]
                position = positions[index]
                if index in blocked_lanes or position == len(page.refs):
                    continue
                more = True
                if len(handles) == _SUBMIT_BUDGET:
                    # A circuit refusal earlier in this tick must not lose
                    # its next opportunity merely because local work filled
                    # the global budget. Otherwise a hot network lane can
                    # repeatedly win over the other network lane.
                    self._next = next((lane for lane in order if lane in blocked_lanes), index)
                    return RotationTick(tuple(handles), examined, corrupt, duplicates, True)
                check()
                ref = page.refs[position]
                outcome = offer(ref) if offer_lane is None else offer_lane(self._kinds[index], ref)
                if (type(outcome) is not RotationOffer
                        or outcome.outcome not in ("accepted", "duplicate", "blocked", "circuit_blocked")
                        or (outcome.outcome == "accepted") != (outcome.handle is not None)):
                    raise ValueError("notice_rotation_invalid_offer")
                if outcome.outcome == "circuit_blocked":
                    # Keep the first unsubmitted key for this lane, but let
                    # a different circuit consume an available global slot.
                    blocked_lanes.add(index)
                    continue
                if outcome.outcome == "blocked":
                    self._next = index
                    return RotationTick(tuple(handles), examined, corrupt, duplicates, True)
                positions[index] += 1
                kind = self._kinds[index]
                self._after[kind] = (page.next_after if positions[index] == len(page.refs)
                                     else ref.key)
                if outcome.outcome == "duplicate":
                    duplicates += 1
                else:
                    handles.append(outcome.handle)
                    self._next = (index+1) % count
            if not more:
                if not handles:
                    self._next = (start+1) % count
                if blocked_lanes:
                    self._next = next(index for index in order if index in blocked_lanes)
                return RotationTick(tuple(handles), examined, corrupt, duplicates, bool(blocked_lanes))
