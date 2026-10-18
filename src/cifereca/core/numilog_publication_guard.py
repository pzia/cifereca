"""Reject untracked readable publications before a new manual Numilog GET.

A file without source provenance is a conflict, never a purchase association.
Only a bounded directory/SQLite inspection happens on explicit acquisition;
no hashing, adoption, deletion or global catalogue scan happens here.
"""
import json
import os
from pathlib import Path
import re
import stat

from ..plugin_api import Cancelled
from ..providers import ProviderConfigurationError
from .numilog_local_preview import _identity_root, _path_in_identity
from .numilog_manifest_rights import _scoped_record_id

MAX_ENTRIES = 4096
MAX_DEPTH = 16
_ERROR = 'Numilog : fichiers locaux non attestés ; rapprochement requis avant téléchargement'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ITEM = re.compile(r'numilog:[0-9a-f]{20}:[A-Za-z0-9_.-]{1,160}:[0-9a-f]{16}\Z')


def _require(value):
    if not value:
        raise ProviderConfigurationError(_ERROR)


def _known_paths(storage, identity_id, root):
    # Read only this owner's Numilog path provenance. Accounts share a provider
    # directory: a known book from another account is not an orphan to adopt.
    with storage._lock, storage._connect() as db:
        db.execute('SAVEPOINT numilog_publications')
        try:
            placements = db.execute(
                "SELECT substr(p.path,1,4097),p.source_item_id,a.artifact_id,a.sha256,a.size,a.media_format "
                "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "WHERE p.identity_id=? AND p.provider_id='numilog' AND p.state='present' LIMIT ?",
                (identity_id, MAX_ENTRIES + 1),
            ).fetchall()
            receipts = db.execute(
                "SELECT credential_id,account_id,item_id,status,substr(metadata_json,1,8193) "
                "FROM acquisition_ledger WHERE identity_id=? AND provider_id='numilog' LIMIT ?",
                (identity_id, MAX_ENTRIES + 1),
            ).fetchall()
        finally:
            db.execute('RELEASE SAVEPOINT numilog_publications')
    _require(len(placements) <= MAX_ENTRIES and len(receipts) <= MAX_ENTRIES)
    known = set()
    for path, item, aid, sha, size, fmt in placements:
        if (type(path) is str and len(path) <= 4096 and _path_in_identity(path, (root,))
                and type(item) is str and _ITEM.fullmatch(item)
                and type(sha) is str and _SHA.fullmatch(sha) and aid == 'sha256:' + sha
                and type(size) is int and size > 0 and fmt in {'epub', 'kepub', 'pdf'}):
            known.add(path)
    for credential, account, item, status, raw in receipts:
        if (type(credential) is not str or not credential or type(account) is not str or not account
                or _scoped_record_id(item, credential) is None
                or status not in {'attention_required', 'complete'}
                or type(raw) is not str or len(raw) > 8192):
            continue
        try:
            meta = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if (type(meta) is dict and meta.get('kind') == 'numilog_delivered_artifact_v1'
                and meta.get('phase') in {'index_pending', 'indexed'} and meta.get('root') == str(root)
                and _path_in_identity(meta.get('path'), (root,))
                and type(meta.get('sha256')) is str and _SHA.fullmatch(meta['sha256'])
                and type(meta.get('size')) is int and meta['size'] > 0):
            known.add(meta['path'])
    return known


def _open_root(root):
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open('/', flags)
    try:
        for part in root.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, flags
    except BaseException:
        os.close(fd)
        raise


def require_tracked_publications(service, identity_id, target_dir):
    """Caller owns the acquisition lock. Never inspect a former owner's root."""
    try:
        roots = _identity_root(service, identity_id)
        _require(roots is not None and len(roots) == 1)
        root = roots[0] / 'numilog'
        _require(isinstance(target_dir, Path))
        _require(target_dir == root)
        known = _known_paths(service.storage, identity_id, root)
        root_fd, flags = _open_root(root)
        count = 0
        def visit(fd, relative, depth):
            nonlocal count
            _require(depth <= MAX_DEPTH)
            with os.scandir(fd) as entries:
                for entry in entries:
                    count += 1
                    _require(count <= MAX_ENTRIES)
                    mode = entry.stat(follow_symlinks=False).st_mode
                    _require(not stat.S_ISLNK(mode))
                    path = relative / entry.name
                    if stat.S_ISDIR(mode):
                        child_fd = os.open(entry.name, flags, dir_fd=fd)
                        try:
                            visit(child_fd, path, depth + 1)
                        finally:
                            os.close(child_fd)
                    elif path.suffix.casefold() in {'.epub', '.pdf', '.kepub'}:
                        _require(stat.S_ISREG(mode) and str(root / path) in known)
        try:
            visit(root_fd, Path(), 0)
        finally:
            os.close(root_fd)
    except Cancelled:
        raise
    except Exception:
        raise ProviderConfigurationError(_ERROR) from None
