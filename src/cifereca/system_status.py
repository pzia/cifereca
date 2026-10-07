"""Concise system inventory; unknown historical dates are never guessed."""
import json
from pathlib import Path
from . import __version__


def installation_date(root):
    try:
        value = json.loads((Path(root) / '.cifereca-install.json').read_text(encoding='utf-8'))
        date = value.get('installed_at') if isinstance(value, dict) else None
        from datetime import datetime
        if not isinstance(date, str):
            return None
        parsed = datetime.fromisoformat(date.replace('Z', '+00:00'))
        return date if parsed.tzinfo else None
    except (OSError, ValueError, TypeError):
        return None


def core_installation_date():
    # Derive from the executing module, never a possibly different current symlink.
    for parent in Path(__file__).resolve().parents:
        if (parent / '.cifereca-install.json').is_file():
            return installation_date(parent)
    return None


def system_status(manager):
    date = core_installation_date()
    rows = manager.diagnostic() if manager is not None else []
    for row in rows:
        if row.get('builtin'):
            row['installed_at'] = date

    packages = {}
    for row in rows:
        package_id = str(row.get('package_id') or '')
        if not package_id:
            continue
        item = packages.setdefault(package_id, {
            'package_id': package_id,
            'package_version': str(row.get('package_version') or ''),
            'builtin': bool(row.get('builtin')),
            'plugins': [],
        })
        item['plugins'].append({
            'plugin_id': str(row.get('plugin_id') or ''),
            'version': str(row.get('version') or ''),
            'state': str(row.get('state') or 'unknown'),
            'enabled': bool(row.get('enabled')),
        })

    return {'version': __version__, 'installed_at': date, 'plugins': rows,
            'packages': list(packages.values()),
            'ready': sum(r.get('state') == 'ready' and bool(r.get('enabled')) for r in rows),
            'attention': sum(r.get('state') in {'blocked', 'error', 'incompatible'} for r in rows)}
