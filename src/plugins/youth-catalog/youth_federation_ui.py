"""GTK broker adapter for the manual federated Youth search (#1028).

Imported on click only. Operations run via the public tasks broker; all
shared-cache mutations and widget updates happen on GTK's idle thread.
"""
from __future__ import annotations

from youth_federation import prepare_sources, collect_federated


def federated_action_available(view):
    from youth_page import source_info
    if not view.identity_id() or getattr(view, "_remote_running_token", None) is not None:
        return False
    sources, _ = prepare_sources(getattr(view, "sources", ()), info=source_info)
    return bool(sources)


def run_federated_action(view):
    from youth_catalog import _extract_page
    from youth_page import fetch_source_page, source_info

    if view.disposed or getattr(view, "_remote_running_token", None) is not None:
        return
    identity, state = view.identity_id(), view.filter_state()
    query = state.query.strip()
    if not identity or not 2 <= len(query) <= 200:
        view.federated_status.set_label(
            "Choisissez un profil et une recherche de 2 à 200 caractères."
        )
        return
    sources, skipped = prepare_sources(getattr(view, "sources", ()), info=source_info)
    if not sources:
        view.federated_status.set_label(
            "Aucune source Jeunesse active et compatible. Le cache reste consultable."
        )
        return
    broker = view.plugin.context.broker("tasks")
    if broker is None:
        view.federated_status.set_label(
            "Service de tâches indisponible. Aucune collecte lancée."
        )
        return

    view.generation += 1
    generation = view.generation
    if view.task:
        view.task.cancel()
    token = (identity, state)
    view._remote_running_token = ("federated", token)
    view.next_cursor = view.next_source_plugin = ""
    progress = {"received": 0, "accepted": 0, "unavailable": skipped}
    view.federated_status.set_label(
        f"Recherche fédérée : 0/{len(sources)} source(s), "
        f"{skipped} indisponible(s) ou non interrogée(s)."
    )
    view.update_action_availability()

    def current(control):
        return (not view.disposed and generation == view.generation
                and not control.cancelled()
                and (view.identity_id(), view.filter_state()) == token)

    def operation(control):
        def on_source(item):
            # Called from worker, so only schedule GTK work. Do not mutate
            # the cache or inspect the current identity on this thread.
            def apply():
                if not current(control):
                    return False
                progress["received"] += 1
                if item.error:
                    progress["unavailable"] += 1
                else:
                    if item.rows:
                        try:
                            report = view.catalog.ingest(
                                identity, item.source_id, item.rows,
                                replace_source=False,
                                fresh_for_seconds=item.fresh_for_seconds,
                            )
                            progress["accepted"] += report.accepted
                            if report.rejected:
                                progress["unavailable"] += 1
                        except Exception:
                            progress["unavailable"] += 1
                    view.render_cached()
                view.federated_status.set_label(
                    f"Recherche fédérée : {progress['received']}/{len(sources)} "
                    f"source(s) interrogée(s), {progress['unavailable']} indisponible(s) "
                    f"ou incomplète(s), {progress['accepted']} notice(s) reçue(s). "
                    "Les métadonnées sont filtrées localement après collecte."
                )
                return False
            view.GLib.idle_add(apply)

        # A published age-minimum threshold is not understood by all
        # upstream APIs: do NOT silently translate it into exact age.
        upstream_age = state.age if getattr(state, "minimum_age", None) is None else None
        return collect_federated(
            sources, query=query, age=upstream_age, themes=state.themes,
            fetch=fetch_source_page, extract=_extract_page,
            cancelled=control.cancelled, on_source=on_source, skipped=skipped,
        )

    try:
        task = broker.submit(
            operation, label="Recherche Jeunesse fédérée", kind="youth.federated",
        )
    except Exception:
        view._remote_running_token = None
        view.federated_status.set_label("Recherche fédérée indisponible.")
        view.update_action_availability()
        return
    view.task = task

    def done(handle):
        try:
            report = handle.result()
        except Exception:
            report = None

        def apply():
            if not current(handle.control):
                return False
            view._remote_running_token = None
            if report is None:
                view.federated_status.set_label(
                    "Recherche fédérée interrompue ; notices déjà reçues conservées."
                )
            else:
                view.federated_status.set_label(
                    f"Recherche terminée : {progress['received']}/{report.eligible} "
                    f"source(s) interrogée(s), {progress['unavailable']} "
                    f"indisponible(s)/incomplète(s), "
                    f"{progress['accepted']} notice(s) reçue(s) en cache."
                )
            view.refresh_source_diagnostics()
            view.update_action_availability()
            return False
        view.GLib.idle_add(apply)

    task.add_done_callback(done)
