"""Single GTK-thread consumer of bounded passive health transitions."""
def notify_health_transitions(window):
    if getattr(window, "_closed", False):
        return
    service = getattr(window, "service", None)
    health = getattr(service, "_provider_health", None)
    toast = getattr(window, "toast", None)
    if health is None or not callable(toast):
        return
    try:
        generation = health.diagnostic_generation()
        if generation is None:
            return
        identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
        epoch = getattr(service, "_account_context_epoch", 0)
        rows = health.drain_transitions(identity_id=identity or "")
        if (not rows or getattr(window, "_closed", False)
                or health.diagnostic_generation() != generation
                or getattr(service, "_provider_health", None) is not health
                or getattr(service, "_account_context_epoch", 0) != epoch
                or getattr(getattr(service, "current_identity", None), "identity_id", None) != identity):
            return
        visible = rows[:3]
        message = "Santé fournisseurs : " + " ; ".join(
            f"{scope.provider} — {outcome.value}" for scope, _, outcome, _ in visible)
        if len(rows) > len(visible):
            message += f" ; {len(rows) - len(visible)} autre(s) transition(s)"
        if health.notifications_saturated():
            message += " ; historique incomplet"
        toast(message)
    except Exception:
        # Optional diagnostic feedback must never interrupt the task lifecycle.
        return
