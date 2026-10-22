"""Opt-in, encrypted diagnostic ZIP of what the collector actually retained.

Unlike the reviewed LLM ZIP, this deliberately contains *private* values
(queries, original text, technical headers) and MUST NEVER be shared as if
anonymized. The ZIP is constructed only in RAM and encrypted *before* any
file write; plaintext ZIP/HAR and provider credentials are never logged.
"""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import stat
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from browser_recorder_vault import (
    VaultError, StoredSession, _key, _aead, _record, MAX_FILE,
)

MAGIC = b"CFRZIP01"
MAX_ENCRYPTED_ZIP = MAX_FILE + 512
README = (
    "PRIVATE CIFERECA BROWSER RECORDER DIAGNOSTIC\n\n"
    "Contains original observed text and URLs. DO NOT share without review.\n"
    "Not an anonymized LLM ZIP, not a full browser HAR/profile or session.\n"
    "Collector omitted Authorization, Cookie, Set-Cookie, binary and out-of-scope traffic.\n"
    "trace.json is the versioned, authenticated source observation snapshot.\n"
)


def _zip_bytes(session: StoredSession) -> bytes:
    if type(session) is not StoredSession:
        raise VaultError("private_zip_invalid")
    if (getattr(session.trace, "vault_session_id", None)
            != session.session_id):
        raise VaultError("private_zip_invalid")
    snapshot = json.loads(_record(
        session.trace, session.session_id, session.state,
    ))
    # Preserve the original vault snapshot timestamp; ZIP creation time is
    # not evidence that the website was visited again.
    snapshot["saved_at"] = session.saved_at
    source = json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, body in (
            ("README.txt", README.encode("utf-8")),
            ("trace.json", source),
        ):
            member = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = ZIP_DEFLATED
            member.external_attr = 0o600 << 16
            archive.writestr(member, body)
    data = output.getvalue()
    if len(data) > MAX_FILE:
        raise VaultError("private_zip_limit")
    return data


def save_session_llm_zip(
    session: StoredSession, destination: Path, *, confirmed=False,
) -> int:
    """Write one explicitly selected *cleartext* session ZIP for a user's LLM.

    Vault remains AES-GCM encrypted at rest; the plain ZIP exists only after
    a Save As confirmation. The destination may be the user's ordinary
    Documents/Downloads folder (no 0700 prerequisite). Create it as 0600,
    exclusively and without following symlinks; never overwrite. No upload.
    """
    if confirmed is not True:
        raise VaultError("private_zip_confirmation_required")
    destination = Path(destination)
    if not destination.is_absolute() or destination.suffix.lower() != ".zip":
        raise VaultError("private_zip_destination")
    # Validate and bound the snapshot before opening the destination.
    payload = _zip_bytes(session)
    folder_fd = None
    try:
        folder_fd = os.open(
            destination.parent, os.O_RDONLY | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(folder_fd)
        if not stat.S_ISDIR(info.st_mode):
            raise VaultError("private_zip_destination")
        fd = os.open(
            destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=folder_fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(folder_fd)
        except Exception:
            try:
                os.unlink(destination.name, dir_fd=folder_fd)
            except OSError:
                pass
            raise
        return len(payload)
    except VaultError:
        raise
    except FileExistsError as exc:
        raise VaultError("private_zip_exists") from exc
    except OSError as exc:
        # No path or OS exception text may appear in the UI/diagnostics.
        raise VaultError("private_zip_write_failed") from exc
    finally:
        if folder_fd is not None:
            os.close(folder_fd)


def save_private_zip(session: StoredSession, directory: Path, passphrase: str,
                     *, confirmed=False) -> str:
    """Save protected .brzip in a *user-selected existing* private directory.

    Never creates the destination folder, never overwrites/symlinks, and
    never touches the reviewed LLM ZIP decisions or export consent.
    """
    if confirmed is not True:
        raise VaultError("private_zip_confirmation_required")
    if not isinstance(directory, Path):
        directory = Path(directory)
    if not directory.is_absolute():
        raise VaultError("private_zip_directory")
    # Derive the fresh export key and serialize the ZIP in private RAM only.
    salt = secrets.token_bytes(16)
    key = _key(passphrase, salt)
    plain_zip = _zip_bytes(session)
    nonce = secrets.token_bytes(12)
    encrypted = (
        MAGIC + salt + nonce + _aead()(key).encrypt(
            nonce, plain_zip, MAGIC + salt + nonce,
        )
    )
    if len(encrypted) > MAX_ENCRYPTED_ZIP:
        raise VaultError("private_zip_limit")
    fd = None
    try:
        fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise VaultError("private_zip_directory")
        name = secrets.token_hex(16) + ".brzip"
        temp = "." + secrets.token_hex(16) + ".partial"
        created = False
        try:
            handle = os.open(
                temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd,
            )
            with os.fdopen(handle, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            # Hardlink + unlink commits an already fsynced encrypted file
            # without the overwrite semantics of os.replace/rename.
            os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd,
                    follow_symlinks=False)
            created = True
            os.unlink(temp, dir_fd=fd)
            os.fsync(fd)
        except Exception:
            try:
                os.unlink(temp, dir_fd=fd)
            except OSError:
                pass
            if created:
                try:
                    os.unlink(name, dir_fd=fd)
                except OSError:
                    pass
            raise
        return name
    except VaultError:
        raise
    except OSError as exc:
        raise VaultError("private_zip_write_failed") from exc
    finally:
        if fd is not None:
            os.close(fd)


def decrypt_private_zip(data: bytes, passphrase: str) -> bytes:
    """Documented interoperable envelope decoder (no disk writes).

    Output is ordinary ZIP data after explicit local decryption; callers
    MUST treat its contents as fully private, never upload automatically.
    """
    if (type(data) is not bytes or not data.startswith(MAGIC)
            or not 64 <= len(data) <= MAX_ENCRYPTED_ZIP):
        raise VaultError("private_zip_invalid")
    salt = data[len(MAGIC):len(MAGIC) + 16]
    nonce = data[len(MAGIC) + 16:len(MAGIC) + 28]
    key = _key(passphrase, salt)
    try:
        zip_bytes = _aead()(key).decrypt(
            nonce, data[len(MAGIC) + 28:], MAGIC + salt + nonce,
        )
    except Exception as exc:
        raise VaultError("private_zip_unlock_failed") from exc
    if len(zip_bytes) > MAX_FILE or not zip_bytes.startswith(bytes((80, 75, 3, 4))):
        raise VaultError("private_zip_invalid")
    return zip_bytes
