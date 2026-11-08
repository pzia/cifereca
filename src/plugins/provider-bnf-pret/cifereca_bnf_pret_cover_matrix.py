"""P1 #4978: read-only, exact-resource cover-projection diagnostic matrix.

The six-coordinate key prevents cross-profile, cross-account and cross-loan
joins. A state is a *reported observation at that stage*, not a proof that
the source actually supplied a usable image or that the account may borrow.
No providers, GUI, cache, filesystem, network, rights or mutations are called.
The public aggregate contains neither identifiers nor cover URLs.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

STAGES = (
    "source_card", "provider_loan", "catalogue", "search",
    "detail", "library", "borrow_view", "gtk_image",
)
STATES = frozenset((
    "observed", "missing", "rejected", "ambiguous", "failed",
))
MAX_KEYS = 64
MAX_ROWS = MAX_KEYS * len(STAGES)


@dataclass(frozen=True, slots=True, repr=False)
class ExactCoverScope:
    identity_id: str
    provider_id: str
    credential_id: str
    account_id: str
    loan_id: str
    resource_id: str

    def __post_init__(self) -> None:
        # Never use a missing account to match a different profile/primary.
        for value in (
            self.identity_id, self.provider_id, self.credential_id,
            self.account_id, self.loan_id, self.resource_id,
        ):
            if (type(value) is not str or not 0 < len(value) <= 160
                    or value != value.strip() or not value.isprintable()
                    or "\x00" in value):
                raise ValueError("invalid_exact_cover_scope")


@dataclass(frozen=True, slots=True, repr=False)
class CoverObservation:
    scope: ExactCoverScope
    stage: str
    state: str

    def __post_init__(self) -> None:
        if (type(self.scope) is not ExactCoverScope
                or type(self.stage) is not str or self.stage not in STAGES
                or type(self.state) is not str or self.state not in STATES):
            raise ValueError("invalid_cover_observation")


@dataclass(frozen=True, slots=True, repr=False)
class CoverMatrixRow:
    scope: ExactCoverScope = field(repr=False)
    states: tuple[tuple[str, str], ...]
    transitions: tuple[str, ...]


def reconcile_exact_cover_matrix(
    scopes: object, observations: object,
) -> tuple[CoverMatrixRow, ...]:
    """Reconcile 0/1/N trusted *stage observations*, with no heuristic join.

    Missing observations are "unobserved", never "missing". Conflicting
    assertions for the exact same scope/stage become "ambiguous" regardless
    of input order; they never override a positive or invent a match.
    Transition flags report *adjacent known facts*, not a diagnosis of cause.
    A local Library cover may exist despite the provider loan lacking one.
    """
    if (type(scopes) not in (list, tuple) or len(scopes) > MAX_KEYS
            or type(observations) not in (list, tuple)
            or len(observations) > MAX_ROWS):
        raise ValueError("invalid_cover_matrix_batch")
    if any(type(scope) is not ExactCoverScope for scope in scopes):
        raise ValueError("invalid_cover_matrix_scope")
    if len(set(scopes)) != len(scopes):
        raise ValueError("duplicate_cover_matrix_scope")
    selected = set(scopes)
    by_scope: dict[ExactCoverScope, dict[str, str]] = {
        scope: {} for scope in scopes
    }
    for observation in observations:
        if (type(observation) is not CoverObservation
                or observation.scope not in selected):
            # A row from a different identity/account must not be silently
            # included under the requested scope or treated as a fallback.
            raise ValueError("foreign_cover_observation")
        state = by_scope[observation.scope]
        previous = state.get(observation.stage)
        if previous is None:
            state[observation.stage] = observation.state
        elif previous != observation.state:
            state[observation.stage] = "ambiguous"
    out = []
    for scope in scopes:
        known = by_scope[scope]
        state = lambda stage: known.get(stage, "unobserved")
        flags = []
        for earlier, later, flag in (
            ("source_card", "provider_loan", "source_to_loan_missing"),
            ("catalogue", "search", "catalogue_to_search_missing"),
            ("search", "detail", "search_to_detail_missing"),
            ("provider_loan", "borrow_view", "loan_to_borrow_missing"),
            ("library", "borrow_view", "library_to_borrow_missing"),
        ):
            if state(earlier) == "observed" and state(later) == "missing":
                flags.append(flag)
        if state("borrow_view") == "observed" and state("gtk_image") == "failed":
            flags.append("gtk_render_failed")
        if state("borrow_view") == "observed" and state("gtk_image") == "missing":
            flags.append("gtk_image_not_observed")
        out.append(CoverMatrixRow(
            scope=scope,
            states=tuple((stage, state(stage)) for stage in STAGES),
            transitions=tuple(flags),
        ))
    return tuple(out)


def public_cover_matrix_summary(
    rows: object,
) -> dict[str, object]:
    """A fixed-shape aggregate for optional diagnostics, without any keys.

    Do not emit row.scope, observed provider URL, title or loan identity.
    This is not an authorization or a statement of actual image availability.
    """
    if (type(rows) not in (tuple, list) or len(rows) > MAX_KEYS
            or any(type(row) is not CoverMatrixRow for row in rows)):
        raise ValueError("invalid_cover_matrix_report")
    known_flags = (
        "source_to_loan_missing", "catalogue_to_search_missing",
        "search_to_detail_missing", "loan_to_borrow_missing",
        "library_to_borrow_missing", "gtk_render_failed",
        "gtk_image_not_observed",
    )
    counts = Counter(flag for row in rows for flag in row.transitions)
    return {
        "schema": "bnf.cover-projection-matrix/1",
        "observed_scopes": len(rows),
        "transition_counts": {key: counts[key] for key in known_flags},
        "incomplete_scopes": sum(
            any(value == "unobserved" for _, value in row.states)
            for row in rows
        ),
        "rights_verified": False,
        "supplier_reachability_verified": False,
    }


__all__ = [
    "ExactCoverScope", "CoverObservation", "CoverMatrixRow",
    "reconcile_exact_cover_matrix", "public_cover_matrix_summary",
    "STAGES", "STATES",
]
