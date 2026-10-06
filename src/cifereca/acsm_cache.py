"""Short-lived ACSM reuse, scoped to an exact loan and borrower.

The sidecar contains hashes and provenance only, never a delivery URL/token.
Unmanifested legacy inputs are deliberately not reused.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

TTL = 24 * 3600
MAX_BYTES = 4 * 1024 * 1024
MANIFEST = '.acsm-cache.json'


def scope(loan, identity_id: str, account_id: str) -> dict:
    return dict(identity=identity_id, provider=loan.source_provider,
                credential=loan.source_credential, borrower=account_id,
                loan=loan.id, item=str(loan.record_id or loan.id), due=loan.due_at,
                url_sha256=hashlib.sha256((loan.ebook_url or '').encode('utf-8')).hexdigest())


def _active(loan, now: float) -> bool:
    if loan.is_late or str(loan.state or '').casefold() in {'returned', 'expired', 'cancelled', 'closed', 'rendu', 'expire', 'expiré'}:
        return False
    if not loan.due_at:
        return True
    try:
        due = datetime.fromisoformat(str(loan.due_at).replace('Z', '+00:00'))
        if len(str(loan.due_at)) == 10:
            due += timedelta(days=1)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        return now < due.timestamp()
    except (ValueError, TypeError, OverflowError):
        return False


def _digest(path: Path, directory: Path, now: float) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
            return None
        if path.suffix.casefold() != '.acsm' or not 0 < path.stat().st_size <= MAX_BYTES:
            return None
        data = path.read_bytes()
        text = data.decode('utf-8-sig')
        if len(data) > MAX_BYTES or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            return None
        root = ElementTree.fromstring(text)
        if root.tag.rsplit('}', 1)[-1] != 'fulfillmentToken':
            return None
        for element in root.iter():
            if element.tag.rsplit('}', 1)[-1].casefold() in {'expiration', 'expires', 'validuntil'}:
                value = (element.text or '').strip()
                expiry = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if now >= expiry.timestamp():
                    return None
        return hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, OverflowError, ElementTree.ParseError):
        return None


def reuse(directory: Path, loan, identity_id: str, account_id: str, *, now=None) -> Path | None:
    now = time.time() if now is None else now
    manifest = directory / MANIFEST
    try:
        if directory.is_symlink() or manifest.is_symlink() or manifest.stat().st_size > 16384 or not _active(loan, now):
            return None
        row = json.loads(manifest.read_text(encoding='utf-8'))
        if row.get('schema') != 1 or row.get('scope') != scope(loan, identity_id, account_id):
            return None
        age = now - float(row['created_at'])
        if not 0 <= age < TTL:
            return None
        name = row['file']
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
            return None
        path = directory / name
        digest = _digest(path, directory, now)
        return path if digest and digest == row.get('sha256') else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def remember(path: Path, directory: Path, loan, identity_id: str, account_id: str, *, now=None) -> bool:
    now = time.time() if now is None else now
    digest = _digest(path, directory, now)
    if directory.is_symlink() or not digest or not _active(loan, now):
        return False
    row = dict(schema=1, scope=scope(loan, identity_id, account_id), file=path.name,
               sha256=digest, created_at=now)
    temporary = directory / (MANIFEST + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(row, stream, ensure_ascii=False)
        os.replace(temporary, directory / MANIFEST)
        return True
    except OSError:
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def forget(path: Path) -> None:
    """Remove only the cache sidecar referring to the successfully used input."""
    manifest = path.parent / MANIFEST
    try:
        if not manifest.is_symlink() and manifest.stat().st_size <= 16384:
            row = json.loads(manifest.read_text(encoding='utf-8'))
            if row.get('file') == path.name:
                manifest.unlink()
    except (OSError, ValueError, AttributeError):
        pass
