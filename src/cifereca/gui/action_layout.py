"""Pure sizing policy shared by page action bars."""

def action_budget(page_width: int, fraction: float = 0.5) -> int:
    return max(0, int(max(0, page_width) * fraction))


def compact_actions(labelled_widths, budget: int, spacing: int = 6) -> bool:
    widths = tuple(max(0, int(value)) for value in labelled_widths)
    return sum(widths) + max(0, len(widths) - 1) * spacing > budget


def visible_action_count(
    labelled_widths,
    budget: int,
    *,
    spacing: int = 6,
    overflow_width: int = 40,
    minimum_visible: int = 1,
) -> int:
    """Return how many leading actions fit before the overflow menu."""
    widths = tuple(max(0, int(value)) for value in labelled_widths)
    if not widths:
        return 0
    minimum = min(max(1, int(minimum_visible)), len(widths))
    total = sum(widths) + max(0, len(widths) - 1) * spacing
    if total <= budget:
        return len(widths)

    visible = minimum
    used = sum(widths[:visible]) + max(0, visible - 1) * spacing
    while visible < len(widths):
        next_used = used + (spacing if visible else 0) + widths[visible]
        hidden_after = len(widths) - (visible + 1)
        reserve = (spacing + overflow_width) if hidden_after > 0 else 0
        if next_used + reserve > budget:
            break
        used = next_used
        visible += 1
    return visible
