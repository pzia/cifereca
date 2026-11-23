"""#3176: unified, private dry-run plan over existing organizer advice.

This is a review artifact, not a LibraryMutationPlan or authorization.
Do not pass it to any writer. The ordinary cleanup hold is untouched.
"""
from dataclasses import dataclass
from pathlib import Path

from .library_organizer_review import (
    OrganizerScope, OrganizerCursor, run_organizer_review,
    select_organizer_keeper_advice,
)
from .library_sqlite_freshness import sqlite_source_stamp


@dataclass(frozen=True, slots=True, repr=False)
class OrganizerDryRunEntry:
    placement_id: str
    operation: str
    current: str
    proposed: str
    state: str


@dataclass(frozen=True, slots=True, repr=False)
class OrganizerDryRunPlan:
    scope: OrganizerScope
    anchor: OrganizerCursor
    entries: tuple[OrganizerDryRunEntry, ...]
    confirmed: bool = False
    executable: bool = False
    group_index: int | None = None
    rename_only: bool = False
    excluded_count: int = 0


def prepare_organizer_dry_run(scope, report, *, confirmed=False, group_index=None,
                              rename_only=False):
    """Fresh explicit page selection. No filesystem or SQLite changes.

    Only the existing naming broker can supply a rename proposal.
    Classification paths and keeper selections are explicitly held, not
    turned into move/delete requests. Conflicting proposed targets remain
    blocked even if each independent preview saw a vacant destination.
    """
    if (confirmed is not True or type(scope) is not OrganizerScope
            or type(report) is not dict or not scope.bound()
            or type(rename_only) is not bool or (rename_only and group_index is not None)
            or (group_index is not None and
                (type(group_index) is not int or not 0 <= group_index < 4))):
        return None
    try:
        anchor = report.get("review_anchor")
        if (type(anchor) is not OrganizerCursor or anchor.scope is not scope
                or sqlite_source_stamp(scope.database) != anchor.stamp
                or report.get("mutation_authorized") is not False):
            return None
        fresh = run_organizer_review(scope.service, scope,
                                     cursor=anchor if anchor.offset else None)
        if fresh != report:
            return None
        if group_index is not None and group_index >= len(fresh["groups"]):
            return None
        from ..artifact_runtime import LibraryMaintenanceBrokerImpl
        from ..plugin_api import LibraryMutationRequest
        broker = LibraryMaintenanceBrokerImpl(scope.service)
        selected = select_organizer_keeper_advice(scope, fresh, confirmed=True)
        if selected is None or selected.executable is not False:
            return None
        entries = []
        for group in fresh["groups"]:
            for file in group["files"]:
                current, name = file["current"], file["proposed_name"]
                if group["copies"] != 1 or not current or not name:
                    continue
                if file["reason"] == "rename_advice_only":
                    plan = broker.plan(LibraryMutationRequest(
                        "normalize_filename", placement_id=file["placement"]))
                    if (plan.operation != "normalize_filename"
                            or plan.destructive is not False
                            or plan.request.placement_id != file["placement"]
                            or plan.changes != (Path(current).name, name)
                            or plan.facts.get("new_filename") != name):
                        return None
                    entries.append(OrganizerDryRunEntry(
                        file["placement"], "rename", current,
                        str(Path(current).with_name(name)), "review_only"))
                if file.get("classified_path_advice"):
                    entries.append(OrganizerDryRunEntry(
                        file["placement"], "classify", current,
                        file["classified_path_advice"], "relocation_held"))
        for choice in selected.preselected:
            keeper = next((f for g in fresh["groups"] for f in g["files"]
                           if f["placement"] == choice.keeper_placement_id), None)
            if keeper is None or not keeper["current"]:
                return None
            for pid in choice.retire_candidate_ids:
                candidate = next((f for g in fresh["groups"] for f in g["files"]
                                  if f["placement"] == pid), None)
                if candidate is None or not candidate["current"]:
                    return None
                entries.append(OrganizerDryRunEntry(
                    pid, "keeper", candidate["current"], keeper["current"], "cleanup_held"))
        # Two independent books may propose the same vacant name. A page
        # cannot preselect either by order. Keep both explicit as conflicts.
        destinations = {}
        # A proposed target occupied by a different source in this same
        # review page is not an available destination, even if the independent
        # naming preview has not observed the other operation yet.
        sources = {}
        for entry in entries:
            if entry.operation in ("rename", "classify"):
                destinations.setdefault(entry.proposed, set()).add(entry.placement_id)
                sources.setdefault(entry.current, set()).add(entry.placement_id)
        entries = tuple(
            OrganizerDryRunEntry(e.placement_id, e.operation, e.current,
                                 e.proposed, "destination_conflict")
            if e.operation in ("rename", "classify")
            and (len(destinations[e.proposed]) > 1
                 or any(pid != e.placement_id for pid in sources.get(e.proposed, ())))
            else e for e in entries)
        if (not scope.bound() or sqlite_source_stamp(scope.database) != anchor.stamp
                or run_organizer_review(scope.service, scope,
                                        cursor=anchor if anchor.offset else None) != fresh):
            return None
        # Resolve an ordinal against the freshly revalidated page, not a
        # path or identifier provided by the caller. Detect collisions on
        # the whole page BEFORE filtering, so selecting a single group
        # cannot turn a contested destination into safe advice.
        if group_index is not None:
            selected_ids = {f["placement"] for f in fresh["groups"][group_index]["files"]}
            entries = tuple(e for e in entries if e.placement_id in selected_ids)
            if not entries:
                return None
        excluded_count = 0
        if rename_only:
            # Filter AFTER evaluating all collisions and held operations.
            # This is a page-local selection, never a claim that another
            # page cannot propose the same destination or that a writer is safe.
            proposed_count = len(entries)
            entries = tuple(e for e in entries
                            if e.operation == "rename" and e.state == "review_only")
            excluded_count = proposed_count - len(entries)
        return OrganizerDryRunPlan(scope, anchor, entries, group_index=group_index,
                                   rename_only=rename_only, excluded_count=excluded_count)
    except Exception:
        return None


def confirm_organizer_dry_run(scope, report, plan, *, confirmed=False):
    """Confirm the displayed dry-run only after fresh exact re-evaluation."""
    if (confirmed is not True or type(plan) is not OrganizerDryRunPlan
            or plan.scope is not scope or plan.confirmed is not False
            or plan.executable is not False or not plan.entries):
        return None
    fresh = prepare_organizer_dry_run(
        scope, report, confirmed=True, group_index=plan.group_index,
        rename_only=plan.rename_only)
    if fresh is None or fresh != plan:
        return None
    return OrganizerDryRunPlan(scope, fresh.anchor, fresh.entries, confirmed=True,
                               group_index=fresh.group_index, rename_only=fresh.rename_only,
                               excluded_count=fresh.excluded_count)
