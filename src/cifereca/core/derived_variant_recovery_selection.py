"""#4520: private per-receipt selection for an operator, not a recovery grant.

Only local CoreUI may display the profile-relative recorded coordinates.
Never log/repr the objects, export to a plugin or use a display label as
an identifier. Physical validity is checked only on the separate review.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .derived_variant_journal import DerivedReservation
from .derived_variant_recovery_operator import _Scope, _capture, review_linked_recovery, RecoveryReview
from .derived_variant_review import _snapshot
from .derived_variant_review_queue import pending_derived_review_page


@dataclass(frozen=True, slots=True, repr=False)
class RecoveryChoice:
    reservation: DerivedReservation
    parent_relative: str | None
    target_relative: str | None
    output_size: int | None
    media_format: str | None


@dataclass(frozen=True, slots=True, repr=False)
class RecoveryChoicePage:
    scope: _Scope
    choices: tuple[RecoveryChoice, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedRecoveryChoice:
    choice: RecoveryChoice
    review: RecoveryReview


def _coordinates(scope, reservation):
    intent, directory = _snapshot(scope.storage, reservation, scope.identity_id)
    if directory != scope.directory:
        raise ValueError("review_scope_changed")
    profile = scope.root / directory
    parent, stage, target = map(Path, (
        intent.parent_path, intent.staged_path, intent.candidate_path,
    ))
    if any(path == profile or not path.is_relative_to(profile)
           for path in (parent, stage, target)):
        raise ValueError("review_scope_changed")
    labels = (str(parent.relative_to(profile)), str(target.relative_to(profile)))
    # Refuse visually deceptive/control-bearing labels rather than trim or
    # replace them and accidentally make two distinct paths look identical.
    if any(not value.isprintable() or len(value) > 4096 for value in labels):
        raise ValueError("review_label_unavailable")
    return RecoveryChoice(reservation, *labels, intent.output_size, intent.media_format)


def recovery_choices(service, *, identity_id, explicitly_requested=False,
                     previous=None, limit=16):
    """Read bounded current receipt coordinates; no filesystem scan or write.

    A pending receipt can have unusable recorded coordinates. Keep that
    row as a blocked choice without displaying its paths. Cursor and scope
    stay in the private page object; no caller-supplied receipt key string.
    """
    if (explicitly_requested is not True or type(identity_id) is not str
            or not 0 < len(identity_id) <= 256
            or type(limit) is not int or not 1 <= limit <= 16):
        return None
    try:
        if previous is None:
            scope = _capture(service, identity_id)
            cursor = ""
        else:
            if (type(previous) is not RecoveryChoicePage
                    or previous.scope.service is not service
                    or previous.scope.identity_id != identity_id
                    or previous.next_cursor is None):
                return None
            scope, cursor = previous.scope, previous.next_cursor
        if scope is None or not scope.current():
            return None
        page = pending_derived_review_page(
            scope.storage, identity_id=identity_id, after_key=cursor, limit=limit,
        )
        choices = []
        for reservation in page["reservations"]:
            if not scope.current():
                return None
            try:
                choices.append(_coordinates(scope, reservation))
            except Exception:
                choices.append(RecoveryChoice(reservation, None, None, None, None))
        if (pending_derived_review_page(scope.storage, identity_id=identity_id,
                                       after_key=cursor, limit=limit) != page
                or not scope.current()):
            return None
        return RecoveryChoicePage(scope, tuple(choices), page["next_key"], page["complete"])
    except Exception:
        return None


def review_recovery_choice(service, page, choice, *, explicitly_requested=False):
    """Revalidate the exact selected object; only linked bytes prepare review.

    The caller retains this private object until a separate confirmation.
    No raw receipt key, path, displayed index or caller-selected digest
    authorizes recovery. Physical hash and inode checks are performed by
    the existing recovery review, not this selection layer.
    """
    if (explicitly_requested is not True or type(page) is not RecoveryChoicePage
            or type(choice) is not RecoveryChoice or page.scope.service is not service
            or not any(item is choice for item in page.choices)
            or choice.parent_relative is None or choice.target_relative is None):
        return None
    try:
        scope = page.scope
        if not scope.current() or _coordinates(scope, choice.reservation) != choice:
            return None
        review = review_linked_recovery(
            service, identity_id=scope.identity_id, reservation=choice.reservation,
            explicitly_requested=True,
        )
        if (type(review) is not RecoveryReview or not scope.current()
                or review.scope != scope
                or _coordinates(scope, choice.reservation) != choice):
            return None
        return ReviewedRecoveryChoice(choice, review)
    except Exception:
        return None


def preview_confirmed_recovery_choice(service, page, reviewed, *, confirmed=False):
    """Recheck a reviewed choice after explicit consent, without any writer.

    This is a dry-run only. Neither a positive result nor a prior review
    authorizes a production recovery. Do not return the fresh private handle.
    """
    if (confirmed is not True or type(page) is not RecoveryChoicePage
            or type(reviewed) is not ReviewedRecoveryChoice
            or type(reviewed.choice) is not RecoveryChoice
            or type(reviewed.review) is not RecoveryReview
            or page.scope.service is not service
            or reviewed.review.scope != page.scope
            or reviewed.review.reservation != reviewed.choice.reservation):
        return "review_required"
    fresh = review_recovery_choice(
        service, page, reviewed.choice, explicitly_requested=True,
    )
    return "verified_no_write" if fresh is not None else "review_required"
