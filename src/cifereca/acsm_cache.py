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


def reuse(directory: Path, loan, identity_id: str, account_id: str, *,
          now=None, claim_state=None) -> Path | None:
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
        if not digest or digest != row.get('sha256'):
            return None
        # An ACSM promoted from a one-shot first offer keeps its immutable
        # claim binding even after post-download hooks succeeded. Merely
        # matching the standard URL-keyed sidecar is not a substitute.
        if row.get('initial_claim_bound') is True:
            if claim_state is None:
                return None
            initial = reuse_initial(
                directory, loan, identity_id, account_id,
                now=now, claim_state=claim_state,
            )
            if initial != path:
                return None
        elif 'initial_claim_bound' in row:
            # Unknown/forged provenance markers can never weaken the check.
            return None
        else:
            # #4644/#4648 may have written an ordinary post-hook cache
            # before the claim-bound marker existed. Do not grandfather in
            # a same-file first-offer receipt as an unqualified cache hit.
            # An existing but unreadable first-offer sidecar is not proof
            # that this old unmarked cache came from an ordinary download.
            # Refuse missing provenance rather than downgrading loan scope.
            initial_manifest = directory / INITIAL_MANIFEST
            if initial_manifest.is_symlink() or initial_manifest.exists():
                initial_row = _read_initial_manifest(directory)
                if initial_row is None:
                    return None
                if initial_row.get('file') == name:
                    if (claim_state is None or reuse_initial(
                            directory, loan, identity_id, account_id,
                            now=now, claim_state=claim_state,
                    ) != path):
                        return None
        return path
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def remember(path: Path, directory: Path, loan, identity_id: str, account_id: str, *,
             now=None, initial_claim_bound=False, claim_state=None) -> bool:
    now = time.time() if now is None else now
    digest = _digest(path, directory, now)
    if (type(initial_claim_bound) is not bool
            or directory.is_symlink() or not digest or not _active(loan, now)):
        return False
    if initial_claim_bound:
        # Not sufficient as authority by itself; only a matching,
        # independently verified first-offer receipt may be promoted.
        if (claim_state is None or reuse_initial(
                directory, loan, identity_id, account_id, now=now,
                claim_state=claim_state,
        ) != path):
            return False
    row = dict(schema=1, scope=scope(loan, identity_id, account_id), file=path.name,
               sha256=digest, created_at=now)
    if initial_claim_bound:
        row['initial_claim_bound'] = True
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


# #3121: a first-borrow Paris ACSM may be issued under a one-shot signed
# redirect URL that is absent from a later ListLoans response. The URL itself
# must never become a persisted receipt, and the normal URL-keyed cache above
# must remain unchanged for all existing provider paths.
INITIAL_MANIFEST = '.initial-acsm-receipt.json'


def _initial_digest(path: Path, directory: Path, now: float) -> str | None:
    """Verify the exact regular file via a no-follow directory and file fd.

    Never accept an ACSM replaced with a symlink between stat and read.
    The standard cache retains its historical ABI; this new receipt is stricter.
    """
    import stat
    if (not isinstance(path, Path) or not isinstance(directory, Path)
            or path.parent != directory or path.suffix.casefold() != '.acsm'
            or not path.name or '/' in path.name or chr(92) in path.name):
        return None
    parent_fd = file_fd = None
    try:
        parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory_info = os.fstat(parent_fd)
        if (not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != os.geteuid()):
            return None
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or not 0 < info.st_size <= MAX_BYTES):
            return None
        with os.fdopen(file_fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
        again = os.fstat(file_fd)
        if (len(data) != info.st_size or info.st_ino != again.st_ino
                or info.st_dev != again.st_dev
                or info.st_size != again.st_size or info.st_mtime_ns != again.st_mtime_ns):
            return None
        upper = data.upper()
        if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
            return None
        root = ElementTree.fromstring(data)
        if root.tag != '{http://ns.adobe.com/adept}fulfillmentToken':
            return None
        for element in root.iter():
            if element.tag.rsplit('}', 1)[-1].casefold() in {
                'expiration', 'expires', 'validuntil',
            }:
                value = (element.text or '').strip()
                expiry = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if now >= expiry.timestamp():
                    return None
        # Durable byte payload before publishing a receipt that promises it.
        os.fsync(file_fd)
        return hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, OverflowError, ElementTree.ParseError):
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _initial_scope(loan, identity_id: str, account_id: str) -> dict | None:
    """Only an exact, active Paris DILICOM borrowing scope is receivable."""
    if (type(identity_id) is not str or not identity_id
            or type(account_id) is not str or not account_id
            or loan.source_provider != 'paris'
            or loan.provider != 'DILICOM' or loan.medium != 'digital'
            or type(loan.source_credential) is not str
            or not loan.source_credential
            or type(loan.identity_id) is not str
            or loan.identity_id != identity_id
            or type(loan.source_account_id) is not str
            or loan.source_account_id != account_id
            or type(loan.id) is not str or not loan.id
            or type(loan.record_id) is not str or not loan.record_id):
        return None
    return dict(identity=identity_id, provider='paris',
                credential=loan.source_credential, borrower=account_id,
                loan=loan.id, item=loan.record_id, due=loan.due_at)


def remember_initial(path: Path, directory: Path, loan, identity_id: str,
                     account_id: str, *, now=None, borrow_claim=None,
                     admission_current=None) -> bool:
    """Remember validated first-offer ACSM bytes, never their bearer URL.

    An initial receipt is not a new loan or evidence of ADEPT fulfillment.
    The caller must have confirmed this exact loan and staged a genuine ACSM
    using its already supplied first-offer URL, with no replay of the borrow.
    """
    now = time.time() if now is None else now
    try:
        # The claim must remain confirmed and the Core identity/session/epoch
        # lease must remain current for the entire publication window.
        if admission_current is not None and (
                not callable(admission_current)
                or admission_current() is not True):
            return False
        owner = _initial_scope(loan, identity_id, account_id)
        digest = _initial_digest(path, directory, now)
        if (owner is None or digest is None or not _active(loan, now)
                or directory.is_symlink() or path.parent != directory
                or (admission_current is not None
                    and admission_current() is not True)):
            return False
        # A production first-offer receipt also records the opaque Core
        # at-most-once claim. Neither the operation ID nor hashed claim key
        # grants a loan; the reader must recheck the confirmed SQLite state.
        if borrow_claim is not None:
            if (type(borrow_claim) is not tuple or len(borrow_claim) != 2
                    or type(borrow_claim[0]) is not str
                    or not borrow_claim[0].startswith("loan.borrow.v1:")
                    or len(borrow_claim[0]) != len("loan.borrow.v1:") + 64
                    or any(ch not in "0123456789abcdef"
                           for ch in borrow_claim[0][len("loan.borrow.v1:"):])
                    or type(borrow_claim[1]) is not str
                    or not 0 < len(borrow_claim[1]) <= 128
                    or not all(ch.isascii() and (ch.isalnum() or ch in "_-")
                               for ch in borrow_claim[1])):
                return False
        row = dict(schema=1, scope=owner, file=path.name, sha256=digest,
                   created_at=now)
        if borrow_claim is not None:
            row["borrow_claim"] = list(borrow_claim)
        temporary = directory / (INITIAL_MANIFEST + '.' + uuid.uuid4().hex + '.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(row, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            # Check again after writing/fsync, immediately before the atomic
            # publication. A revoked lease cannot authorize a new receipt.
            if (admission_current is not None
                    and admission_current() is not True):
                return False
            os.replace(temporary, directory / INITIAL_MANIFEST)
            parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY
                                | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            return True
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _read_initial_manifest(directory: Path) -> dict | None:
    """Read a bounded regular manifest through pinned, no-follow descriptors.

    Both directory and manifest are opened without following symlinks. A
    concurrent replacement of the pathname cannot redirect this read to an
    arbitrary target; a malformed or unstable read is rejected.
    """
    import stat
    parent_fd = manifest_fd = None
    try:
        parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid():
            return None
        manifest_fd = os.open(
            INITIAL_MANIFEST, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(manifest_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or not 0 < before.st_size <= 16384):
            return None
        with os.fdopen(manifest_fd, 'rb', closefd=False) as stream:
            raw = stream.read(16385)
        after = os.fstat(manifest_fd)
        if (len(raw) != before.st_size or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            return None
        row = json.loads(raw.decode('utf-8'))
        return row if type(row) is dict else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        if manifest_fd is not None:
            os.close(manifest_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def reuse_initial(directory: Path, loan, identity_id: str, account_id: str,
                  *, now=None, claim_state=None) -> Path | None:
    """Read a first-offer receipt by loan provenance, not an expiring URL."""
    now = time.time() if now is None else now
    try:
        owner = _initial_scope(loan, identity_id, account_id)
        if owner is None or not _active(loan, now):
            return None
        row = _read_initial_manifest(directory)
        if row is None:
            return None
        if (type(row) is not dict or row.get('schema') != 1
                or row.get('scope') != owner):
            return None
        # Callers with a live Core storage broker MUST present a confirmed
        # claim for the very operation that created the one-shot token.
        # An existing legacy receipt without this binding is not an
        # independent entitlement and cannot be adopted by this strict path.
        if claim_state is not None:
            claim = row.get("borrow_claim")
            if (type(claim) is not list or len(claim) != 2
                    or type(claim[0]) is not str
                    or not claim[0].startswith("loan.borrow.v1:")
                    or len(claim[0]) != len("loan.borrow.v1:") + 64
                    or any(ch not in "0123456789abcdef"
                           for ch in claim[0][len("loan.borrow.v1:"):])
                    or type(claim[1]) is not str or not claim[1]
                    or not callable(claim_state)):
                return None
            state = claim_state(claim[0])
            if (type(state) is not dict
                    or state.get("schema") != 1
                    or state.get("operation") != "loan.borrow"
                    or state.get("operation_id") != claim[1]
                    or state.get("status") != "confirmed"
                    or state.get("request_accepted") is not True
                    or state.get("verification") != "positive_active_loan"
                    or state.get("verified_loan_id") != loan.id):
                return None
        created_at = row['created_at']
        if (type(created_at) not in (float, int)
                or not 0 <= now - created_at < TTL):
            return None
        name = row.get('file')
        if (type(name) is not str or not name
                or Path(name).name != name
                or '/' in name or chr(92) in name
                or not name.casefold().endswith('.acsm')):
            return None
        candidate = directory / name
        actual = _initial_digest(candidate, directory, now)
        return (candidate if actual is not None
                and actual == row.get('sha256') else None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def forget_initial(path: Path) -> None:
    """Best-effort receipt cleanup in the pinned private staging directory.

    Never follow a sidecar symlink, trust a pathname-only read, or delete a
    manifest for a different ACSM. This is not an atomic inode-CAS unlink:
    the private staging directory must remain owner-controlled.
    """
    import stat
    if (not isinstance(path, Path) or path.suffix.casefold() != '.acsm'
            or not path.name or '/' in path.name or chr(92) in path.name):
        return
    parent_fd = manifest_fd = None
    try:
        parent_fd = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid():
            return
        manifest_fd = os.open(
            INITIAL_MANIFEST, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(manifest_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or not 0 < before.st_size <= 16384):
            return
        with os.fdopen(manifest_fd, 'rb', closefd=False) as stream:
            raw = stream.read(16385)
        after = os.fstat(manifest_fd)
        if (len(raw) != before.st_size or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            return
        row = json.loads(raw.decode('utf-8'))
        if type(row) is not dict or row.get('file') != path.name:
            return
        current = os.stat(
            INITIAL_MANIFEST, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(current.st_mode)
                or current.st_dev != before.st_dev
                or current.st_ino != before.st_ino
                or current.st_size != before.st_size
                or current.st_mtime_ns != before.st_mtime_ns):
            return
        os.unlink(INITIAL_MANIFEST, dir_fd=parent_fd)
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        pass
    finally:
        if manifest_fd is not None:
            os.close(manifest_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def qualify_claimed_initial_receipt_offer(
    service, account: Account, session, source: SourceKey,
    right: ProviderRightObservation, *, current,
) -> ProviderRightObservation | None:
    """Project an exact verified local first-token receipt as a delivery candidate.

    A provider snapshot saying 'unknown delivery' must not become 'available'
    based on a cache file alone. The source is already provider-attested, the
    user has opted in, and the original loan.borrow claim is confirmed.
    """
    from dataclasses import replace
    import stat
    from .models import Account, Loan
    from .plugin_api.provider_rights_observation import ProviderRightObservation
    from .core.source_convergence import SourceKey

    scope = source.scope
    if (type(account) is not Account or type(source) is not SourceKey
            or type(right) is not ProviderRightObservation
            or not right.provider_attested or right.delivery != "unknown"
            or scope.provider_id != account.provider_id or source.kind != "loan"
            or type(source.branch) is not str or not source.branch
            or account.unique_id != ""
            or (right.edition_id, right.right_id, right.kind) != (
                source.edition_id, source.right_id, source.kind)
            or (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id)
            or not callable(current) or current() is not True):
        return None

    # No cache = no additional provider read. This branch is exclusively
    # for the one-shot manual-first token; ordinary selected delivery keeps
    # its pre-existing offer semantics.
    try:
        directory = service._acquisition_work_dir(
            scope.identity_id, scope.provider_id, source.right_id,
        )
        if not isinstance(directory, Path):
            return None
        info = os.stat(
            directory / INITIAL_MANIFEST,
            follow_symlinks=False,
        )
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or not 0 < info.st_size <= 16384 or current() is not True):
            return None
    except (OSError, AttributeError, TypeError, ValueError):
        return None

    # Read-only real provider inventory: the right must STILL be exactly one
    # loan for the same credential/borrower/edition, not merely a local file.
    try:
        loans = session.loans(account)
        if type(loans) not in (list, tuple) or len(loans) > 10000:
            return None
        matches = [
            loan for loan in loans
            if type(loan) is Loan and loan.id == source.right_id
            and loan.record_id == source.edition_id
            and type(loan.provider) is str
            and loan.provider.casefold() == source.branch.casefold()
            and loan.medium == "digital"
            and loan.source_provider == scope.provider_id
            and loan.source_credential == scope.credential_id
            and loan.source_account_id == "primary"
            and loan.identity_id == scope.identity_id
        ]
        if len(matches) != 1 or current() is not True:
            return None
        candidate = reuse_initial(
            directory, matches[0], scope.identity_id, "primary",
            claim_state=service.storage.loan_borrow_state,
        )
        if candidate is None or current() is not True:
            return None
    except Exception:
        # The exception can contain signed borrower data. Fail closed, never
        # log it and never ask the plugin to redo the content GET here.
        return None

    # This opaque value identifies only the checked Core offer admission;
    # the actual bytes, claim and local inode are checked again by the
    # canonical executor before ADEPT. No private path or signed URL stored.
    encoded = json.dumps(
        [scope.identity_id, scope.provider_id, scope.credential_id,
         scope.account_id, source.branch, source.edition_id, source.right_id,
         source.kind],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    offer_id = f"core.{scope.provider_id}.firstacsm:" + hashlib.sha256(encoded).hexdigest()
    return replace(right, delivery="available", delivery_offer_id=offer_id)



def stage_claimed_initial_acsm(service, session, account: Account, source, *,
                           attempt: Path, admission_current) -> Path | None:
    """Copy one exact claimed initial ACSM into a fresh canonical attempt.

    None means there is no initial receipt for this exact source loan.
    An existing but unusable receipt fails closed: a second content GET must
    not silently replace an uncertain first borrow token.
    """
    from .models import Account, Loan
    import stat

    scope = source.scope
    if (type(account) is not Account
            or scope.provider_id != account.provider_id
            or source.kind != "loan" or type(source.branch) is not str
            or not source.branch):
        return None
    # Only the exact implicit primary account can have a first-offer
    # receipt from the confirmed manual borrow action. Other borrowers
    # legitimately use ordinary canonical selected-right acquisition;
    # absence of this special receipt must never block their delivery.
    if type(account) is Account and account.unique_id != "":
        if (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id):
            raise ValueError("canonical_initial_scope_invalid")
        return None
    if (type(account) is not Account
            or (account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id)
            or not callable(admission_current)):
        raise ValueError("canonical_initial_scope_invalid")
    if admission_current() is not True:
        raise ValueError("canonical_initial_admission_revoked")

    # The service constructs these 0700 dirs with openat(O_NOFOLLOW); never
    # accept a directory provided by a plugin or derive a path from a title.
    directory = service._acquisition_work_dir(
        scope.identity_id, scope.provider_id, source.right_id)
    sidecar = directory / INITIAL_MANIFEST
    try:
        sidecar_stat = os.stat(sidecar, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(sidecar_stat.st_mode)
            or sidecar_stat.st_uid != os.geteuid()
            or not 0 < sidecar_stat.st_size <= 16384):
        raise ValueError("canonical_initial_receipt_invalid")

    # A fresh exact borrower inventory is descriptive and read-only; never
    # attempt another borrow, token GET, or provider-selected candidate GET.
    loans = session.loans(account)
    if type(loans) not in (list, tuple) or len(loans) > 10000:
        raise ValueError("canonical_initial_inventory_unavailable")
    candidates = [
        loan for loan in loans
        if type(loan) is Loan and loan.id == source.right_id
        and loan.record_id == source.edition_id
        and type(loan.provider) is str
        and loan.provider.casefold() == source.branch.casefold()
        and loan.medium == "digital"
        and loan.source_provider == scope.provider_id
        and loan.source_credential == scope.credential_id
        and loan.identity_id == scope.identity_id
        and loan.source_account_id == "primary"
    ]
    if len(candidates) != 1 or admission_current() is not True:
        raise ValueError("canonical_initial_loan_unverified")
    loan = candidates[0]
    claim_state = service.storage.loan_borrow_state
    candidate = reuse_initial(
        directory, loan, scope.identity_id, "primary",
        claim_state=claim_state)
    if candidate is None:
        raise ValueError("canonical_initial_receipt_unverified")

    parent_fd = file_fd = attempt_fd = target_fd = None
    try:
        parent_fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(parent_fd)
        if (not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()):
            raise ValueError("canonical_initial_parent_untrusted")
        file_fd = os.open(
            candidate.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or not 0 < before.st_size <= MAX_BYTES):
            raise ValueError("canonical_initial_bytes_untrusted")
        with os.fdopen(file_fd, "rb", closefd=False) as source_stream:
            payload = source_stream.read(MAX_BYTES + 1)
        after = os.fstat(file_fd)
        if (len(payload) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)):
            raise ValueError("canonical_initial_bytes_changed")
        digest = hashlib.sha256(payload).hexdigest()
        # The cache's independent XML, expiry, scope and claim checks run
        # again after our own pinned read, not only before opening the file.
        if (_initial_digest(candidate, directory, __import__("time").time())
                != digest
                or reuse_initial(
                    directory, loan, scope.identity_id, "primary",
                    claim_state=claim_state) != candidate
                or admission_current() is not True):
            raise ValueError("canonical_initial_receipt_changed")
        attempt_fd = os.open(
            attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        bound = os.fstat(attempt_fd)
        current = os.stat(attempt, follow_symlinks=False)
        if (bound.st_uid != os.geteuid()
                or (bound.st_dev, bound.st_ino)
                != (current.st_dev, current.st_ino)
                or not stat.S_ISDIR(current.st_mode)):
            raise ValueError("canonical_initial_target_untrusted")
        target_fd = os.open(
            "first-offer.acsm",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=attempt_fd,
        )
        with os.fdopen(target_fd, "wb", closefd=False) as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        if (os.fstat(target_fd).st_size != len(payload)
                or admission_current() is not True
                or reuse_initial(
                    directory, loan, scope.identity_id, "primary",
                    claim_state=claim_state) != candidate
                or os.fstat(file_fd).st_ino != before.st_ino):
            raise ValueError("canonical_initial_receipt_changed")
        return attempt / "first-offer.acsm"
    except OSError:
        raise ValueError("canonical_initial_copy_unavailable") from None
    finally:
        for fd in (target_fd, attempt_fd, file_fd, parent_fd):
            if fd is not None:
                os.close(fd)

