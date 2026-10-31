"""#3185: explicit, user-confirmed recovery of canonical dedup tombstones.

The GTK Maintenance host owns the destructive confirmation and shared
acquisition lock around re-planning and execution. This plugin never
inspects physical paths or deletes files. Only the Core supervisor can
decide whether a pending tombstone is still safely recoverable.
"""
from __future__ import annotations

from cifereca.plugin_api import (
    LibraryMutationRequest, MaintenancePlan, MaintenanceResult,
    MaintenanceToolInfo,
)


class CanonicalDedupRecoveryTool:
    info = MaintenanceToolInfo(
        id="library.duplicates.recover",
        title="Reprendre les nettoyages de doublons interrompus",
        description=(
            "Inspecte les suppressions canoniques en attente et propose "
            "une reprise explicitement confirmée, sans nouveau droit."
        ),
        icon_name="view-refresh-symbolic",
        scope="library",
        danger_level="destructive",
        supports_dry_run=True,
        cancellable=True,
    )

    def __init__(self):
        self.context = None

    def activate(self, context):
        self.context = context
        return self

    def deactivate(self):
        self.context = None

    def describe(self):
        return self.info

    def _broker(self):
        if self.context is None:
            raise RuntimeError("maintenance_unavailable")
        broker = self.context.broker("library.maintenance")
        if broker is None or not callable(
            getattr(broker, "pending_canonical_cleanups", None)
        ):
            raise RuntimeError("canonical_cleanup_review_unavailable")
        return broker

    @staticmethod
    def _valid_id(value):
        return (
            type(value) is str and 0 < len(value) <= 256
            and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        )

    def plan(self, context):
        broker = self._broker()
        identity_id = getattr(context, "identity_id", None)
        if identity_id is not None and not self._valid_id(identity_id):
            raise ValueError("canonical_cleanup_invalid_identity")
        # Do not trust a plug-in or stale cached inventory to authorize
        # the Core recovery. Status must be fully reviewed with 0 unknown.
        review = broker.pending_canonical_cleanups(identity_id=identity_id)
        state = getattr(review, "status", None)
        unverified = getattr(review, "unverified_count", None)
        listed = getattr(review, "candidates", None)
        pairs = []
        keepers = set()
        good = (
            state == "reviewed" and type(unverified) is int and unverified == 0
            and type(listed) in (tuple, list) and len(listed) <= 1000
        )
        if good:
            seen = set()
            for candidate in listed:
                pair = (
                    getattr(candidate, "candidate_placement_id", None),
                    getattr(candidate, "keeper_placement_id", None),
                    getattr(candidate, "identity_id", None),
                )
                if (
                    not all(self._valid_id(value) for value in pair)
                    or pair[0] == pair[1] or pair[0] in seen
                    or (identity_id is not None and pair[2] != identity_id)
                ):
                    good = False
                    break
                pairs.append(pair)
                seen.add(pair[0])
                keepers.add(pair[1])
        if good and seen.intersection(keepers):
            good = False
        if not good:
            # Never turn a partial or unclassifiable queue into a safe
            # destructive plan. Expose only an opaque failed preflight.
            return MaintenancePlan(
                tool_id=self.info.id,
                summary="Inventaire des reprises de doublons indisponible.",
                warnings=("Éléments non vérifiables : aucune reprise.",),
                facts={
                    "review_status": "unavailable", "identity_id": identity_id,
                    "pairs": (),
                },
            )
        return MaintenancePlan(
            tool_id=self.info.id,
            summary=f"{len(pairs)} nettoyage(s) canonique(s) à reprendre.",
            changes=tuple(
                f"Vérifier et reprendre le nettoyage n°{n}"
                for n in range(1, min(len(pairs), 100) + 1)
            ),
            warnings=(
                ("Chaque fichier est à nouveau vérifié par Core sous verrou.",
                 "Aucune restauration automatique ni inférence de droit.")
                if pairs else ()
            ),
            facts={
                "review_status": "reviewed", "identity_id": identity_id,
                "pairs": tuple(pairs),
            },
        )

    def run(self, plan, control):
        control.check_cancelled()
        facts = plan.facts
        if (
            type(plan.tool_id) is not str or plan.tool_id != self.info.id
            or type(facts) is not dict
            or set(facts) != {"review_status", "identity_id", "pairs"}
            or facts["review_status"] != "reviewed"
            or type(facts["pairs"]) is not tuple
            or len(facts["pairs"]) > 1000
        ):
            return MaintenanceResult(
                False, 0, "Reprise non exécutée : plan invalide.",
                warnings=("Aucune suppression : plan de reprise invalide.",),
                facts={"attempted": 0, "failures": 1},
            )
        identity_id = facts["identity_id"]
        pairs = facts["pairs"]
        seen = set()
        keepers = set()
        for pair in pairs:
            if (
                type(pair) is not tuple or len(pair) != 3
                or not all(self._valid_id(value) for value in pair)
                or pair[0] == pair[1] or pair[0] in seen
                or (identity_id is not None and pair[2] != identity_id)
            ):
                return MaintenanceResult(
                    False, 0, "Reprise non exécutée : plan invalide.",
                    warnings=("Aucune suppression : plan de reprise invalide.",),
                    facts={"attempted": 0, "failures": 1},
                )
            seen.add(pair[0])
            keepers.add(pair[1])
        if seen.intersection(keepers):
            return MaintenanceResult(
                False, 0, "Reprise non exécutée : plan invalide.",
                warnings=("Aucune suppression : plan de reprise invalide.",),
                facts={"attempted": 0, "failures": 1},
            )
        broker = self._broker()
        # The host confirms a destructive plan, then replans while holding
        # the same acquisition lock. Repeat that read-only check here before
        # the first mutation. No untrusted "already_locked" flag is accepted.
        current = self.plan(
            type("_RecoveryContext", (), {"identity_id": identity_id})()
        )
        if current.facts != facts:
            return MaintenanceResult(
                False, 0, "Reprise non exécutée : inventaire modifié.",
                warnings=("L’inventaire a changé : relancer la prévisualisation.",),
                facts={"attempted": 0, "failures": 1},
            )
        completed = 0
        changed = 0
        failures = 0
        total = len(pairs)
        control.check_cancelled()
        if total == 0:
            control.report(message="Aucun nettoyage interrompu à reprendre")
        for index, (candidate_id, keeper_id, _owner) in enumerate(pairs, start=1):
            control.check_cancelled()
            control.report(
                current=index - 1, total=total,
                message=f"Reprise {index}/{total}",
            )
            try:
                request = LibraryMutationRequest(
                    "canonical_dedup",
                    placement_id=candidate_id,
                    keep_placement_id=keeper_id,
                    options={"action": "recover", "confirmed": True},
                )
                prepared = broker.plan(request)
                outcome = broker.run(prepared, control)
                state = getattr(outcome, "facts", None)
                if (
                    getattr(outcome, "ok", None) is not True
                    or type(state) is not dict or state.get("status") != "retired"
                    or type(getattr(outcome, "changed", None)) is not int
                    or outcome.changed not in (0, 1)
                ):
                    failures += 1
                else:
                    completed += 1
                    changed += outcome.changed
            except Exception:
                control.check_cancelled()
                failures += 1
        control.check_cancelled()
        if total:
            control.report(current=total, total=total, message=(
                "Reprises terminées" if failures == 0
                else f"Reprise partielle : {failures} échec(s)"
            ))
        return MaintenanceResult(
            failures == 0,
            changed,
            (
                f"{completed} nettoyage(s) canonique(s) terminé(s)."
                if failures == 0
                else f"{completed} nettoyage(s) repris, {failures} échec(s)."
            ),
            warnings=(
                (f"{failures} nettoyage(s) exigent une vérification manuelle.",)
                if failures else ()
            ),
            facts={"attempted": total, "completed": completed, "failures": failures},
        )


RECOVERY_TOOL = CanonicalDedupRecoveryTool()
