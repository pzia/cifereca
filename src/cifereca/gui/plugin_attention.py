"""Pure expansion policy for the Plugins inventory (no GTK dependency).

A user's saved collapse choice matters for ordinary browsing, but it cannot
hide actionable plugin failures or matching search/filter results.
"""


def plugin_section_expanded(
    saved: bool | None,
    *,
    default: bool = False,
    attention: bool = False,
    filtered: bool = False,
) -> bool:
    """Show problems and requested results before respecting saved collapse."""
    if attention or filtered:
        return True
    return bool(default) if saved is None else bool(saved)


__all__ = ["plugin_section_expanded"]
