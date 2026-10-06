"""Common user-facing help with compatibility for old manifests."""
def plugin_help(value):
    if isinstance(value, dict):
        return value.get('help_text') or value.get('description') or 'Aide non fournie.'
    return value.help_text or value.description or 'Aide non fournie.'
