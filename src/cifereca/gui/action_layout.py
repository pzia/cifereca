"""Pure sizing policy shared by page action bars."""

def action_budget(page_width: int, fraction: float = 0.5) -> int:
    return max(0, int(max(0, page_width) * fraction))


def compact_actions(labelled_widths, budget: int, spacing: int = 6) -> bool:
    widths = tuple(max(0, int(value)) for value in labelled_widths)
    return sum(widths) + max(0, len(widths) - 1) * spacing > budget
