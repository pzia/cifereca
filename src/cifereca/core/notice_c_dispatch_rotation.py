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
_PRIORITY = {"interactive_visible": 0, "recent_acquisition": 1,
             "author_neighborhood": 2, "background_stale": 3}
_MAX_PRIORITY_HINTS = 128


def validate_priority_hints(hints):
    """Only bounded, exact-generation scheduling hints; never authority."""
    if type(hints) is not tuple or len(hints) > _MAX_PRIORITY_HINTS:
        raise ValueError("notice_rotation_invalid_priority_hints")
    result = {}
    for item in hints:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("notice_rotation_invalid_priority_hints")
        ref, reason = item
        journal._check_ref(ref)
        if type(reason) is not str or reason not in _PRIORITY or ref in result:
            raise ValueError("notice_rotation_invalid_priority_hints")
        result[ref] = _PRIORITY[reason]
    return result


@dataclass(frozen=True, slots=True, repr=False)
class RotationPage:
    refs: tuple[journal.JobRef, ...]
    next_after: str | None
    examined: int
    corrupt: int
    priority_hints: tuple = ()


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
        self._priority_turn = True

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

    def tick(self, read_page, offer, *, check_cancelled=None, offer_lane=None,
             priority_hints=()):
        if (not callable(read_page) or not callable(offer)
                or (check_cancelled is not None and not callable(check_cancelled))
                or (offer_lane is not None and not callable(offer_lane))):
            raise ValueError("notice_rotation_invalid_port")
        ranks = validate_priority_hints(priority_hints)
        promote = self._priority_turn
        self._priority_turn = not self._priority_turn
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
            if kind != _PURCHASE_LANE:
                for ref, rank in validate_priority_hints(page.priority_hints).items():
                    if ref not in page.refs:
                        raise ValueError("notice_rotation_invalid_page")
                    ranks[ref] = min(rank, ranks.get(ref, 3))
            examined += page.examined
            corrupt += page.corrupt
            if not page.refs:
                self._after[kind] = page.next_after

        def checked_offer(index, ref):
            outcome = offer(ref) if offer_lane is None else offer_lane(self._kinds[index], ref)
            if (type(outcome) is not RotationOffer
                    or outcome.outcome not in ("accepted", "duplicate", "blocked", "circuit_blocked")
                    or (outcome.outcome == "accepted") != (outcome.handle is not None)):
                raise ValueError("notice_rotation_invalid_offer")
            return outcome

        # At most one promotion from the already scanned ready pages, on
        # alternate ticks. Never advance the normal cursor for a promoted
        # out-of-order key. Unpromoted ticks preserve the original sweep,
        # even with one available slot and a continuously hot visible key.
        # Private purchase intents retain their independent fair lane.
        promoted = None
        if promote and ranks:
            candidates = [(ranks[ref], offset, ref.key, index, ref)
                          for offset, index in enumerate(order)
                          if self._kinds[index] != _PURCHASE_LANE
                          for ref in pages[index].refs if ranks.get(ref, 3) < 3]
            if candidates:
                _, _, _, index, ref = min(candidates)
                check()
                outcome = checked_offer(index, ref)
                if outcome.outcome == "accepted":
                    handles.append(outcome.handle)
                    promoted = ref
                elif outcome.outcome == "duplicate":
                    duplicates += 1
                    promoted = ref

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
                outcome = (RotationOffer("duplicate") if ref == promoted
                           else checked_offer(index, ref))
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
                    duplicates += int(ref != promoted)
                else:
                    handles.append(outcome.handle)
                    self._next = (index+1) % count
            if not more:
                if not handles:
                    self._next = (start+1) % count
                if blocked_lanes:
                    self._next = next(index for index in order if index in blocked_lanes)
                return RotationTick(tuple(handles), examined, corrupt, duplicates, bool(blocked_lanes))
