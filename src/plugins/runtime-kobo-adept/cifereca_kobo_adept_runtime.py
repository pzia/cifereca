from __future__ import annotations

import json
import logging
import hashlib
import hmac
import secrets
import uuid
from pathlib import Path
from datetime import date
import shutil
import subprocess
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar

from cifereca_runtime_diagnostics import safe_message, output_bytes, operation_family, stderr_summary
from cifereca_kobo_adept_loan_envelope import attested_readonly_loans_list
from cifereca.plugin_api import (
    ArtifactInput, AttentionRequired, DrmMutationRequest, DrmMutationResult,
    PluginContext, UserFacingError,
)


# Operation owner is an explicitly annotated Core/plugin caller category, not
# a guessed identity from argv, profile names or timing proximity.
_ADEPT_CALLER = ContextVar("cifereca_adept_caller", default="unattributed")


@contextmanager
def _adept_caller(category):
    if category not in ("activation", "core_probe"):
        raise ValueError("invalid closed ADEPT caller category")
    marker = _ADEPT_CALLER.set(category)
    try:
        yield
    finally:
        _ADEPT_CALLER.reset(marker)


def _readonly_scope(args, payload):
    """Private scope coordinates for the measured reads, never for mutation."""
    if (payload is not None or type(args) is not list
            or any(type(value) is not str for value in args)):
        return None
    if args in (['contract', '--json'], ['capabilities', '--json'],
                ['profile', 'list', '--json']):
        return ('runtime', '')
    if args == ['loans', 'list', '--json']:
        return ('default_profile', '')
    if (len(args) == 5 and args[:3] == ['loans', 'list', '--profile']
            and args[4] == '--json'):
        return ('explicit_profile', args[3])
    return None


class KoboAdeptError(UserFacingError):
    def __init__(self, message, *, code, retryable=False, exit_code=None, technical_detail='', details=None):
        super().__init__(safe_message(message), code=code, technical_detail=technical_detail)
        self.retryable = retryable
        self.exit_code = exit_code
        self.details = dict(details or {})


_ACSM_CODES = frozenset({
    'E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER',
    'adept_license_already_fulfilled_by_another_user',
    'adept_error',
})

# The external CLI is a process boundary, not a trusted message formatter.
# Only these *observed in the local contract simulator* profile errors have
# safe, stable semantics. All other codes and messages stay opaque until
# explicitly qualified with the upstream CLI contract.
_KNOWN_NON_ACSM_ERRORS = {
    'profile_missing': 'Profil Kobo-Adept introuvable.',
    'invalid_profile': 'Profil Kobo-Adept invalide.',
}

# Contract cifereca 1.3 §4 (Kobo-Adept 0.13.5/0.13.6): structured
# ADEPT/profile errors are qualified independently of process exit 10.
# Messages remain consumer-owned; never parse stderr or the vendor message.
_CONTRACT_ADEPT_ERRORS = {
    'invalid_acsm': 'Le jeton ACSM est invalide.',
    'profile_not_found': 'Profil Kobo-Adept introuvable.',
    'adept_profile_required': 'Un profil ADEPT est requis.',
    'adept_profile_error': 'Le profil ADEPT ne permet pas cette opération.',
    'adept_profile_incompatible': 'Le profil ADEPT est incompatible avec cette opération.',
    'adept_error': "Échec ADEPT ; la cause Adobe précise n'est pas confirmée.",
}

# Diagnostics must carry a CLOSED public vocabulary, not vendor JSON values,
# stderr fragments or arbitrary exception.code. Only codes produced by our
# own run_json failure classifier can cross the support/export boundary.
_RUNTIME_DIAGNOSTIC_FAILURE_CODES = frozenset({
    *_CONTRACT_ADEPT_ERRORS,
    'adept_error',
    'adept_license_already_fulfilled_by_another_user',
    'profile_missing', 'invalid_profile',
    'kobo_adept_failed', 'kobo_adept_missing',
    'kobo_adept_timeout', 'kobo_adept_launch_error',
    'kobo_adept_invalid_json', 'kobo_adept_invalid_utf8',
    'kobo_adept_invalid_response', 'kobo_adept_output_too_large',
})

# This bound applies to *every* JSON command, not only loan inventory reads.
# subprocess.run still captures process output before this check; it prevents
# unbounded UTF-8 decoding, JSON parsing and returning oversized vendor data.
_MAX_JSON_RESPONSE_BYTES = 4 * 1024 * 1024

# Multi-profile inventory results are returned to Core. Error messages and
# even machine-looking exception codes are untrusted at this plugin boundary:
# neither vendor text nor arbitrary error.code may enter persisted receipts.
_INVENTORY_PUBLIC_ERRORS = {
    'adept_profile_required': 'Profil ADEPT requis.',
    # Contract 1.3 §4: these typed profile/registry failures remain
    # distinguishable in a per-profile read-only loan inventory. They are
    # never inferred from a process exit, exception text or an ACSM payload.
    'profile_not_found': 'Profil Kobo-Adept introuvable.',
    'adept_profile_error': 'Le profil ADEPT ne permet pas cette opération.',
    'adept_profile_incompatible': 'Le profil ADEPT est incompatible avec cette opération.',
    'adept_error': "Échec ADEPT ; la cause Adobe précise n'est pas confirmée.",
    'kobo_adept_missing': 'Exécutable Kobo-Adept indisponible.',
    'kobo_adept_timeout': 'Inventaire ADEPT : délai dépassé.',
    'kobo_adept_launch_error': 'Exécution Kobo-Adept impossible.',
    'kobo_adept_invalid_json': 'Inventaire ADEPT : JSON invalide.',
    'kobo_adept_invalid_utf8': 'Inventaire ADEPT : UTF-8 invalide.',
    'kobo_adept_invalid_response': 'Inventaire ADEPT : réponse invalide.',
    'kobo_adept_invalid_loan_inventory': 'Inventaire ADEPT : entrées invalides.',
    'kobo_adept_output_too_large': 'Inventaire ADEPT : réponse trop volumineuse.',
    'kobo_adept_failed': 'Lecture de l’inventaire ADEPT échouée.',
}
_INVENTORY_FALLBACK_ERROR = 'adept_inventory_unavailable'
_INVENTORY_FALLBACK_MESSAGE = 'Inventaire ADEPT indisponible ; vérifier le profil et les diagnostics.'


def _public_inventory_error(exc):
    """Fixed-code projection; never read str(exc), repr(exc) or vendor fields."""
    try:
        code = getattr(exc, 'code', None)
    except Exception:
        code = None
    if type(code) is str and code in _INVENTORY_PUBLIC_ERRORS:
        result = {'code': code, 'message': _INVENTORY_PUBLIC_ERRORS[code]}
        # Only our typed, closed-code CLI error conveys a qualified retry
        # decision. Arbitrary UserFacingError subclasses have no authority
        # to supply retry semantics, even when they spoof a known code.
        if isinstance(exc, KoboAdeptError):
            result['retryable'] = exc.retryable is True
        return result
    return {'code': _INVENTORY_FALLBACK_ERROR, 'message': _INVENTORY_FALLBACK_MESSAGE}


def _acsm_fulfillment(args, payload):
    """Recognize explicit fulfillment commands, never an exit/message guess."""
    if type(args) not in (list, tuple) or len(args) < 2:
        return False
    if args[0] == 'acsm' and args[1] == 'fetch':
        return True
    return (
        args[0] == 'download' and args[1] == 'request'
        and type(payload) is dict and type(payload.get('source')) is dict
        and payload['source'].get('type') == 'acsm'
    )


def classified_adept_failure(error, exit_code):
    """Classify a FAILED, proven ACSM download request only.

    Caller must independently establish an explicit ACSM download/request
    or acsm/fetch operation. Do not use exit code 10, vendor
    messages or libgourou stderr to *infer* which operation failed.
    Unknown/legacy ACSM failures are fixed and non-retryable; an exact
    machine-readable license enum is the sole more specific diagnosis.
    """
    if not isinstance(error, dict):
        error = {}
    code = error.get('code') if type(error.get('code')) is str else None
    if code in {
        'E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER',
        'adept_license_already_fulfilled_by_another_user',
    }:
        return (
            'adept_license_already_fulfilled_by_another_user',
            "Le prêt ACSM est déjà associé à une autre identité Adobe. Vérifiez l'identité d'origine.",
            False,
        )
    if code in _CONTRACT_ADEPT_ERRORS:
        # An ACSM attempt may have consumed a one-shot licence before failure.
        # The generic retryable flag does not attest a safe new fulfillment.
        return code, _CONTRACT_ADEPT_ERRORS[code], False
    # Never relay unknown vendor fields, raw XML or a potentially sensitive
    # message. Even exit 1 / unknown code is an opaque ACSM error.
    return (
        'adept_error',
        "Échec de l'activation ACSM ; la cause Adobe n'est pas confirmée. Vérifiez le profil d'origine.",
        False,
    )


class KoboAdeptRuntime:
    """Public contract v8, integration document 1.3; no device side effects."""
    required_contract = 8

    def __init__(self, context: PluginContext):
        self.context = context
        self.config = dict(context.config)
        self.last_run_diagnostic = {}
        self.contract_version = None
        # Unlinkable across runtime instances/restarts; never persisted/exported.
        try:
            self._read_scope_salt = secrets.token_bytes(32)
        except Exception:
            self._read_scope_salt = None

    def _read_scope_reference(self, command, args, payload):
        try:
            scope = _readonly_scope(args, payload)
            if scope is None or any(type(value) is not str for value in command):
                return None
            coordinates = json.dumps([command, scope], ensure_ascii=True).encode('ascii')
            return 'rs_' + hmac.new(
                self._read_scope_salt, coordinates, hashlib.sha256,
            ).hexdigest()[:24]
        except Exception:
            return None  # Optional measurement must not affect CLI behavior.

    def _report(self, message, status="ok", *, level="detailed"):
        # Diagnostics crossing the Core boundary contain only bounded technical facts.
        allowed = {
            "operation", "duration_ms", "stdout_bytes", "stderr_bytes", "returncode",
            "stdout_mode", "stdout_summary", "stderr_summary", "json_line", "json_column",
            "launch_error", "loan_count", "inventory_valid",
            "error_code", "retryable", "validation_reason",
            "caller_category", "read_scope_ref",
        }
        detail = {key: value for key, value in self.last_run_diagnostic.items() if key in allowed}
        reference = detail.get('read_scope_ref')
        if (type(reference) is not str or len(reference) != 27
                or not reference.startswith('rs_')
                or any(char not in '0123456789abcdef' for char in reference[3:])):
            detail.pop('read_scope_ref', None)
        if (detail.get("error_code") != "kobo_adept_invalid_response"
                or type(detail.get("validation_reason")) is not str
                or detail["validation_reason"] not in {
                    "json_not_object", "ok_missing", "ok_not_boolean",
                    "success_with_error", "exit_nonzero_with_success",
                }):
            detail.pop("validation_reason", None)
        reporter = getattr(self.context, "report_diagnostic", None)
        if not callable(reporter):
            return
        try:
            reporter(message, status, detail=detail, level=level)
        except Exception:
            # Diagnostics must never alter the runtime operation.
            logger = getattr(self.context, "logger", None)
            if logger is not None:
                # A failing diagnostic callback may embed a signed URL,
                # certificate or private profile in its exception message.
                # Do not attach its traceback or exception representation.
                logger.debug("Kobo-Adept diagnostic reporter unavailable")

    @property
    def command(self):
        value = self.config.get('command') or ['kobo-adept']
        if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
            raise AttentionRequired('Commande Kobo-Adept invalide.', code='kobo_adept_invalid_command')
        return list(value)

    def configured_command(self):
        # The Core reads this *only* for its unavailable-runtime status.
        # Passing full argv exposes private home directories and user-supplied
        # command arguments via diagnostics, even without invoking the CLI.
        self.command  # Validate the configuration without publishing it.
        return ['<commande Kobo-Adept configurée ; arguments masqués>']

    def available(self): return bool(shutil.which(self.command[0]))

    def run_json(self, args, *, payload=None, timeout=60, max_output_bytes=None):
        started = time.monotonic()
        self.last_run_diagnostic = {
            'operation': operation_family(args),
            'caller_category': _ADEPT_CALLER.get(),
        }
        if max_output_bytes is None:
            output_limit = _MAX_JSON_RESPONSE_BYTES
        elif (type(max_output_bytes) is not int
              or not 1 <= max_output_bytes <= _MAX_JSON_RESPONSE_BYTES):
            # Reject invalid budgets before starting a subprocess, even if
            # a caller tries to disable the cap with a huge value or bool.
            raise ValueError("invalid Kobo-Adept JSON response limit")
        else:
            output_limit = max_output_bytes

        def statistics(stdout, stderr, returncode=None):
            out, err = output_bytes(stdout), output_bytes(stderr)
            self.last_run_diagnostic.update(
                duration_ms=int((time.monotonic()-started)*1000),
                stdout_bytes=len(out), stderr_bytes=len(err), stderr_summary=stderr_summary(err))
            if returncode is not None:
                self.last_run_diagnostic['returncode'] = returncode
            return out, err

        def fail(message, code, *, returncode=None, retryable=False):
            # The caller branches translate untrusted CLI values into fixed
            # codes. Revalidate here: one future branch must not accidentally
            # copy a vendor code or a signed URL into diagnostics/support.
            safe_code = (
                code if type(code) is str
                and code in _RUNTIME_DIAGNOSTIC_FAILURE_CODES
                else 'kobo_adept_failed'
            )
            self.last_run_diagnostic['error_code'] = safe_code
            self.last_run_diagnostic['retryable'] = retryable is True
            detail = dict(self.last_run_diagnostic)
            context = []
            if returncode is not None:context.append(f'code retour={returncode}')
            context.append('stdout=' + detail.get('stdout_summary', 'inconnu'))
            context.append('stderr=' + detail.get('stderr_summary', 'inconnu'))
            self._report(f"Kobo-Adept {detail.get('operation') or 'operation'}", "error", level="normal")
            raise KoboAdeptError(message + ' (' + '; '.join(context) + ')', code=safe_code,
                exit_code=returncode, retryable=retryable is True, details=detail,
                technical_detail=json.dumps(detail, ensure_ascii=False)) from None

        try:
            # Byte transport is independent of the parent locale. Only the
            # request is encoded; raw process streams never become diagnostics.
            command = self.command
            reference = self._read_scope_reference(command, args, payload)
            if reference is not None:
                self.last_run_diagnostic['read_scope_ref'] = reference
            proc = subprocess.run([*command, *args],
                input=json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None,
                capture_output=True, timeout=timeout, check=False)
        except FileNotFoundError:
            # The process never started: no stdout/stderr exists. Preserve
            # the historical AttentionRequired contract, but expose the
            # already-closed CLI-missing code via the safe diagnostic
            # callback rather than an unqualified "kobo adept" failure.
            # Never read the exception's .filename, .args or repr: argv
            # and configured binaries may contain private local paths.
            self.last_run_diagnostic.update(
                duration_ms=int((time.monotonic()-started)*1000),
                error_code='kobo_adept_missing',
                retryable=False,
            )
            self._report(
                f"Kobo-Adept {self.last_run_diagnostic['operation']}",
                "error", level="normal",
            )
            raise AttentionRequired("Kobo-Adept n'est pas installé.", code='kobo_adept_missing') from None
        except subprocess.TimeoutExpired as exc:
            statistics(exc.output, exc.stderr)
            self.last_run_diagnostic.update(stdout_mode='timeout', stdout_summary='sortie partielle après délai')
            # A timed-out ACSM fulfillment may already have consumed a
            # one-time license upstream. Never advertise an automatic retry:
            # inspect the exact local artifact/receipt before any new attempt.
            # This is a transport uncertainty, NOT an Adobe error diagnosis.
            fail("Kobo-Adept a dépassé le délai d'exécution.",
                 'kobo_adept_timeout', retryable=not _acsm_fulfillment(args, payload))
        except OSError:
            # Exception subclass names are not a closed diagnostic vocabulary;
            # even a local launch error may carry sensitive path/profile data.
            # Emit a fixed technical family without exception text or argv.
            self.last_run_diagnostic.update(
                duration_ms=int((time.monotonic()-started)*1000),
                launch_error='os_error',
            )
            fail('Kobo-Adept ne peut pas être exécuté.', 'kobo_adept_launch_error')

        out, err = statistics(proc.stdout, proc.stderr, proc.returncode)
        if len(out) > output_limit:
            self.last_run_diagnostic.update(stdout_mode='oversized', stdout_summary='sortie trop volumineuse')
            fail('La réponse Kobo-Adept dépasse la limite de lecture.', 'kobo_adept_output_too_large', returncode=proc.returncode)
        try:
            text = out.decode('utf-8')
        except UnicodeDecodeError:
            self.last_run_diagnostic.update(stdout_mode='invalid_utf8', stdout_summary='UTF-8 invalide')
            fail('La réponse Kobo-Adept ne respecte pas UTF-8.', 'kobo_adept_invalid_utf8', returncode=proc.returncode)
        try:
            value = json.loads(text)
        except (ValueError, TypeError) as exc:
            summary = 'vide' if not text.strip() else 'JSON invalide'
            self.last_run_diagnostic.update(stdout_mode='invalid_json', stdout_summary=summary)
            if isinstance(exc, json.JSONDecodeError):
                self.last_run_diagnostic.update(json_line=exc.lineno, json_column=exc.colno)
            message = ("Kobo-Adept a échoué et n'a pas fourni de JSON exploitable." if proc.returncode != 0
                       else 'Kobo-Adept a renvoyé une réponse JSON invalide.')
            fail(message, 'kobo_adept_invalid_json', returncode=proc.returncode)
        if not isinstance(value, dict):
            self.last_run_diagnostic.update(
                stdout_mode='unexpected_json_type',
                stdout_summary='JSON de type inattendu',
                validation_reason='json_not_object',
            )
            fail('Kobo-Adept a renvoyé un document inattendu.', 'kobo_adept_invalid_response', returncode=proc.returncode)
        self.last_run_diagnostic.update(stdout_mode='json', stdout_summary='objet JSON')
        # Descriptive read-only documents are not operation receipts:
        # contract --json is an attested schema without "ok", and loans list
        # can publish a fully validated loans[] document without "ok" too.
        # All mutating CLI commands still require an explicit native boolean
        # acknowledgement. No fallback is allowed for malformed/ambiguous
        # inventory JSON, including the independently read-only loans expired.
        raw_version = value.get('contract_version')
        descriptive_contract = (
            type(args) is list and args == ['contract', '--json']
            and payload is None and proc.returncode == 0
            and 'ok' not in value and 'error' not in value
            and type(value.get('name')) is str and value['name'] == 'kobo-adept'
            and (
                (type(raw_version) is int and 0 <= raw_version <= 1000)
                or (type(raw_version) is str and 1 <= len(raw_version) <= 3
                    and raw_version.isascii() and raw_version.isdecimal())
            )
        )
        descriptive_loan_list = attested_readonly_loans_list(
            args, payload=payload, returncode=proc.returncode, value=value,
        )
        # Exit 0 alone is never proof that a profile was created, an ACSM
        # fulfilled or a loan returned. Null, integer and string "ok" values
        # stay invalid. A missing ok is allowed only for one exact, validated
        # read-only schema; do not infer success from arbitrary JSON.
        if (proc.returncode == 0 and value.get('ok') is not True
                and value.get('ok') is not False
                and not descriptive_contract and not descriptive_loan_list):
            self.last_run_diagnostic['validation_reason'] = (
                'ok_missing' if 'ok' not in value else 'ok_not_boolean'
            )
            fail('Kobo-Adept n’a pas confirmé le résultat de la commande.',
                 'kobo_adept_invalid_response', returncode=proc.returncode)
        # A payload reporting both success and a vendor error is contradictory.
        # Reject it without forwarding the vendor's potentially private error
        # object through Core or through the diagnostic reporter.
        if proc.returncode == 0 and value.get('ok') is True and value.get('error') is not None:
            self.last_run_diagnostic['validation_reason'] = 'success_with_error'
            fail('Kobo-Adept a renvoyé un résultat contradictoire.',
                 'kobo_adept_invalid_response', returncode=proc.returncode)
        if proc.returncode != 0 and value.get('ok') is True:
            self.last_run_diagnostic['validation_reason'] = 'exit_nonzero_with_success'
            # An upstream nonzero process exit contradicts the CLI's
            # explicit success claim. Do not project even a recognized
            # Adobe/identity error.code from an inconsistent response.
            fail('Kobo-Adept a renvoyé un résultat contradictoire.',
                 'kobo_adept_invalid_response', returncode=proc.returncode)
        if proc.returncode != 0 or value.get('ok') is False:
            error = value.get('error') if isinstance(value.get('error'), dict) else {}
            # Legacy 0.13.6 may publish code/message at the JSON root.
            # Only a PROVEN ACSM request can interpret these as an ADEPT
            # failure. Never classify unrelated commands by exit 10 alone.
            if _acsm_fulfillment(args, payload):
                if not error and isinstance(value.get('code'), str):
                    error = value
                code, public_message, retryable = classified_adept_failure(
                    error, proc.returncode,
                )
                fail(public_message, code, returncode=proc.returncode, retryable=retryable)
            vendor_code = error.get('code')
            if (args and args[0] in {'profile', 'acsm', 'loans'}
                    and type(vendor_code) is str
                    and vendor_code in _CONTRACT_ADEPT_ERRORS):
                fail(_CONTRACT_ADEPT_ERRORS[vendor_code], vendor_code,
                     returncode=proc.returncode,
                     retryable=error.get('retryable') is True)
            # An Adobe-specific enum from a non-ACSM command is contradictory.
            # Exit 10 also has no certified meaning for other operations.
            # Neither case is permission to echo opaque vendor messages.
            if (proc.returncode == 10
                    or (type(error.get('code')) is str
                        and error['code'] in _ACSM_CODES)):
                fail('Échec Kobo-Adept ; cause non qualifiée pour cette opération.',
                     'kobo_adept_failed', returncode=proc.returncode)
            # An unrecognized structured `error.code` may itself contain
            # private URLs, XML, tokens or human-readable identities. Neither
            # that raw key nor the vendor message crosses the Core boundary.
            # Preserve existing machine-readable profile_missing semantics
            # without trusting vendor wording or invented retry semantics for
            # unknown failures.
            vendor_code = error.get('code')
            if type(vendor_code) is str and vendor_code in _KNOWN_NON_ACSM_ERRORS:
                fail(_KNOWN_NON_ACSM_ERRORS[vendor_code], vendor_code,
                     returncode=proc.returncode,
                     retryable=error.get('retryable') is True)
            fail('Échec Kobo-Adept ; code fournisseur non qualifié.',
                 'kobo_adept_failed', returncode=proc.returncode)
        # P1 #2845: preserve timing evidence for the four read-only commands
        # observed repeatedly on startup. A normal-level diagnostic contains
        # only the fixed operation family and bounded technical counters;
        # no CLI argv, profile, account, token, response or subject text.
        # This does NOT prove that calls share the same scope/freshness and
        # must never be used to coalesce inventory or suppress an explicit refresh.
        # Loan inventory is actually invoked with --profile <private name>
        # before --json. Match its structure without retaining or exposing
        # the profile value; do not equate two such calls by their family.
        normal_read = _readonly_scope(args, payload) is not None
        self._report(
            f"Kobo-Adept {self.last_run_diagnostic.get('operation') or 'operation'}",
            "ok", level="normal" if normal_read else "detailed",
        )
        return value

    def contract(self):
        value = self.run_json(['contract', '--json'], timeout=20)
        # Older CLIs may serialize a numeric contract version as a short
        # decimal string; retain this compatibility without applying int()
        # to opaque supplier values or publishing them on a failure.
        raw = value.get('contract_version')
        if type(raw) is int and 0 <= raw <= 1000:
            version = raw
        elif (type(raw) is str and 1 <= len(raw) <= 3
              and raw.isascii() and raw.isdecimal()):
            version = int(raw)
        else:
            raise KoboAdeptError(
                'Version de contrat Kobo-Adept invalide.',
                code='kobo_adept_invalid_contract',
            ) from None
        if value.get('name') != 'kobo-adept':
            raise AttentionRequired(
                'Le programme ne publie pas le contrat Kobo-Adept.',
                code='kobo_adept_invalid_contract',
            )
        if version < self.required_contract:
            raise AttentionRequired(
                'Le contrat Kobo-Adept est trop ancien.',
                code='kobo_adept_contract_old',
            )
        self.contract_version = version
        # Publish the already validated canonical int to the strict Core
        # probe, without changing the external CLI/cache dictionary in place.
        return {**value, 'contract_version': version}

    def capabilities(self):
        value = self.run_json(['capabilities', '--json'])
        self._capabilities_cache = value
        return value

    def _caps(self):
        value = getattr(self, '_capabilities_cache', None)
        if value is None: value = self.capabilities()
        caps = value.get('capabilities', value)
        if not isinstance(caps, dict):
            raise KoboAdeptError('Capabilities Kobo-Adept invalides.',code='kobo_adept_invalid_response')
        return caps

    def _available(self, *keys):
        value = self._caps()
        for key in keys:
            if not isinstance(value, dict): return False
            value = value.get(key)
        return value is True or (isinstance(value, dict) and value.get('available') is True)

    def _require_download(self, payload):
        source = payload.get('source')
        if not isinstance(source, dict) or source.get('type') not in {'http', 'acsm', 'kobo'}:
            raise AttentionRequired('Recette de téléchargement invalide.',code='invalid_request')
        kind = source['type']
        handling = str(source.get('handling') or (payload.get('metadata') or {}).get('handling') or '').casefold()
        if kind == 'http' and handling in {'kobo_kdrm','adobe_adept'}:
            raise AttentionRequired('Un candidat DRM ne peut pas devenir HTTP direct.',code='unsupported_drm')
        if not payload.get('destination'):
            raise AttentionRequired('Destination exacte requise.',code='invalid_request')
        if not self._available('download', kind):
            raise AttentionRequired('Capability de téléchargement indisponible.',code='unsupported_format')
        if kind == 'acsm':
            profile = str(payload.get('profile') or '')
            adept = self._caps().get('adept')
            ready = adept.get('fetch_capable_profiles', []) if isinstance(adept, dict) else []
            if not profile or profile not in ready:
                raise AttentionRequired('Le profil ADEPT choisi ne permet pas le fulfillment ACSM.',code='adept_profile_required')
        if kind == 'kobo':
            candidate = source.get('candidate') or {}
            drm = str(candidate.get('drm_type') or '').casefold()
            if drm in {'adobedrm', 'adobe_adept', 'adept'}:
                raise AttentionRequired('AdobeDrm nécessite un candidat ACSM.',code='unsupported_drm')
            if drm == 'kdrm':
                context = source.get('kdrm') or {}
                if not self._available('download', 'kobo', 'drm', 'KDRM'):
                    raise AttentionRequired('Backend KDRM indisponible.',code='kobo_kdrm_backend_unavailable')
                if not source.get('request') or not context.get('device_id') or not context.get('user_id'):
                    raise AttentionRequired('Recette et contexte KDRM requis.',code='kobo_kdrm_context_required')
            if self._has_content_keys(source):
                raise AttentionRequired('Les clés Kobo doivent être récupérées localement par Adept.',code='invalid_request')
        return kind

    @staticmethod
    def _has_content_keys(value):
        if isinstance(value, dict):
            return any(str(k).casefold().replace('_','') == 'contentkeys' or KoboAdeptRuntime._has_content_keys(v) for k,v in value.items())
        if isinstance(value, list): return any(KoboAdeptRuntime._has_content_keys(v) for v in value)
        return False

    @staticmethod
    def _request_dict(request):
        if isinstance(request, DrmMutationRequest):
            return {'operation': request.operation, 'identity_id': request.identity_id,
                'provider_id': request.provider_id, 'loan_id': request.loan_id,
                'source': dict(request.source), 'destination': str(request.destination) if request.destination else None,
                'media_format': request.media_format, 'profile_ref': request.profile_ref,
                'metadata': dict(request.metadata)}
        return dict(request)

    def supports(self, request):
        payload = self._request_dict(request)
        op = str(payload.get('operation') or '').casefold()
        try:
            if op == 'return': return self._available('adept', 'loan_return')
            if op == 'fulfill':
                self._require_download(self._fulfill_payload(payload)); return True
        except UserFacingError: return False
        # No canonical renewal primitive is published by integration contract 1.1.
        return False

    @staticmethod
    def _return_binding(payload):
        metadata = payload.get('metadata')
        # DrmMutationRequest.loan_id can be a PROVIDER id. Never use it as ADEPT id.
        # Never call str() or silently strip untrusted identity references:
        # a forged object/number or a control character must not reach argv.
        loan = metadata.get('adept_loan_id') if type(metadata) is dict else None
        profile = metadata.get('adept_profile_name') if type(metadata) is dict else None
        if any(
            type(value) is not str
            or not 1 <= len(value) <= 256
            or value != value.strip()
            or value.startswith('-')
            or any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in value)
            for value in (loan, profile)
        ):
            raise AttentionRequired(
                'Loan ID ADEPT et nom du profil de fulfillment requis.',
                code='adept_return_binding_required',
            )
        return loan, profile

    @staticmethod
    def _fulfill_payload(payload):
        return {'schema_version': 2, 'source': dict(payload.get('source') or {}),
            'destination': payload.get('destination'), 'format': payload.get('media_format') or payload.get('format') or 'epub',
            'profile': (payload.get('metadata') or {}).get('adept_profile_name') or payload.get('profile'),
            'metadata': dict(payload.get('metadata') or {})}

    def return_diagnostic(self):
        """Bounded phase/proof flags; no profile, loan ID or raw process output."""
        return dict(getattr(self, '_return_diagnostic', {}))

    def return_loan(self, loan_id, profile):
        logger = getattr(self.context, 'logger', None) or logging.getLogger('cifereca.plugin.runtime.kobo-adept')
        started = time.monotonic()
        trace=uuid.uuid4().hex[:12]
        self._return_diagnostic = {'phase': 'binding', 'trace_id':trace, 'cli_requested': False, 'cli_confirmed': False}
        def phase(name, **detail):
            self._return_diagnostic.update(phase=name, **detail)
            logger.info('ADEPT return trace=%s phase=%s cli_requested=%s cli_confirmed=%s duration_ms=%s',
                        trace,name, self._return_diagnostic['cli_requested'], self._return_diagnostic['cli_confirmed'],
                        int((time.monotonic()-started)*1000))
        try:
            phase('binding')
            loan, name = self._return_binding({'metadata': {'adept_loan_id': loan_id, 'adept_profile_name': profile}})
            phase('capability_check')
            if not self._available('adept', 'loan_return'):
                raise AttentionRequired('Retour ADEPT indisponible.',code='adept_loan_return_unsupported')
            logger.info('ADEPT return trace=%s binding loan_ref=%s profile_ref=%s executable_ref=%s',trace,
                        hashlib.sha256(loan.encode()).hexdigest()[:12],hashlib.sha256(name.encode()).hexdigest()[:12],
                        hashlib.sha256(str(shutil.which(self.command[0]) or self.command[0]).encode()).hexdigest()[:12])
            phase('cli_dispatch', cli_requested=True)
            value = self.run_json(['acsm', 'return', loan, '--profile', name, '--json'],timeout=120)
            process=dict(getattr(self,'last_run_diagnostic',{}) or {})
            logger.info('ADEPT return trace=%s cli-process exit_code=%s duration_ms=%s stdout_bytes=%s stderr_bytes=%s',trace,
                        process.get('returncode'),process.get('duration_ms'),process.get('stdout_bytes'),process.get('stderr_bytes'))
            matches = {'ok_confirmed': value.get('ok') is True,
                       'operation_matches': value.get('operation') == 'loan_return',
                       'loan_matches': value.get('loan_id') == loan,
                       'profile_matches': value.get('profile') == name}
            phase('response_validation', **matches)
            if not all(matches.values()):
                raise KoboAdeptError('Résultat de restitution incohérent.',code='kobo_adept_invalid_response')
            phase('cli_confirmation', cli_confirmed=True)
            # The CLI reports dispatch success; contract 1.3 also specifies an
            # exact returned entry in the originating profile's local registry.
            # Never let Core persist a returned state before this check.
            phase('inventory_verification', inventory_confirmed=False)
            try:
                inventory = self.loans(name)
                matches = [row for row in inventory['loans'] if row['id'] == loan]
            except Exception:
                # The vendor's exception may embed a signed URL, license XML,
                # profile or Adobe credentials. Do not chain its traceback
                # into a user-visible failure or diagnostic record.
                raise KoboAdeptError(
                    'Commande de restitution confirmée, mais inventaire ADEPT illisible. '
                    'Vérifiez le prêt avant toute nouvelle restitution ; le fichier est conservé.',
                    code='adept_return_verification_unavailable', retryable=False) from None
            exact=len(matches)==1
            returned=matches[0].get('returned') if exact else None
            token_present=matches[0].get('token_present') if exact else None
            logger.info('ADEPT return trace=%s inventory matches=%s returned=%s token_present=%s',trace,len(matches),returned,token_present)
            self._return_diagnostic.update(inventory_matches=len(matches),registry_returned=returned,token_present=token_present)
            if not exact or returned is not True or token_present is not False:
                raise KoboAdeptError(
                    'Commande de restitution confirmée, mais état restitué et absence du jeton non confirmés dans ADEPT. '
                    'Vérifiez le prêt avant toute nouvelle restitution ; le fichier est conservé.',
                    code='adept_return_not_confirmed', retryable=False)
            phase('inventory_confirmation', inventory_confirmed=True)
            return value
        except Exception as exc:
            failed_phase = self._return_diagnostic['phase']
            # Codes originate from structured exceptions, never the raw message.
            code = getattr(exc, 'code', 'adept_return_failed')
            safe_codes = {'adept_return_verification_unavailable', 'adept_return_not_confirmed', 'adept_return_binding_required', 'adept_loan_return_unsupported', 'kobo_adept_invalid_response', 'kobo_adept_missing', 'kobo_adept_timeout', 'kobo_adept_launch_error', 'kobo_adept_invalid_json', 'kobo_adept_invalid_utf8', 'kobo_adept_failed'}
            code = code if type(code) is str and code in safe_codes else 'adept_return_failed'
            phase('failed', failed_phase=failed_phase, error_code=code[:120])
            logger.warning('ADEPT return trace=%s failed phase=%s code=%s', trace,failed_phase, code[:120])
            if self._return_diagnostic['cli_requested']:
                process=dict(getattr(self,'last_run_diagnostic',{}) or {})
                logger.warning('ADEPT return trace=%s failure-process exit_code=%s duration_ms=%s stdout_bytes=%s stderr_bytes=%s',trace,process.get('returncode'),process.get('duration_ms'),process.get('stdout_bytes'),process.get('stderr_bytes'))
            raise
        finally:
            self._return_diagnostic['duration_ms'] = int((time.monotonic()-started)*1000)

    @staticmethod
    def _qualified_reference(value):
        """Keep only bounded, typed ADEPT profile/loan identifiers."""
        if value is None:
            return None
        if (type(value) is not str or not 1 <= len(value) <= 256
                or value != value.strip() or value.startswith('-')
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise KoboAdeptError(
                'Identifiant ADEPT non qualifié dans le résultat.',
                code='kobo_adept_invalid_response',
            )
        return value

    def execute(self, request):
        payload = self._request_dict(request)
        op = str(payload.get('operation') or '').casefold()
        if op == 'return':
            loan, name = self._return_binding(payload)
            value = self.return_loan(loan, name)
            # The return path has already confirmed the exact local registry.
            # Only these four receipt fields are used by the Core to perform
            # its own fresh post-return inventory verification. An arbitrary
            # successful vendor JSON may contain secrets/credential objects.
            if (type(value) is not dict or value.get('ok') is not True
                    or value.get('operation') != 'loan_return'
                    or value.get('loan_id') != loan or value.get('profile') != name):
                raise KoboAdeptError(
                    'Résultat de restitution ADEPT incohérent.',
                    code='kobo_adept_invalid_response',
                )
            return DrmMutationResult(ok=True, operation='return', metadata={
                'runtime_result': {
                    'ok': True, 'operation': 'loan_return',
                    'loan_id': loan, 'profile': name,
                },
                'adept_loan_id': loan, 'adept_profile_name': name,
                'loan_state': 'returned', 'artifact_preserved': True,
            })
        if op == 'fulfill':
            value = self.download_request(self._fulfill_payload(payload))
            if type(value) is not dict or value.get('ok') is not True:
                raise KoboAdeptError(
                    'Résultat de téléchargement ADEPT non confirmé.',
                    code='kobo_adept_invalid_response',
                )
            artifact = value.get('artifact')
            if type(artifact) is not dict or type(artifact.get('path')) is not str:
                raise KoboAdeptError(
                    'Artefact ADEPT non qualifié.',
                    code='kobo_adept_invalid_response',
                )
            # Format and content hash are public artifact descriptors, not
            # arbitrary vendor labels. An unknown value must not turn a
            # private token into a Core metadata attribute.
            media_format = artifact.get('format') or ''
            sha256 = artifact.get('sha256') or ''
            if (type(media_format) is not str
                    or media_format.casefold() not in ('', 'epub', 'pdf', 'kepub')
                    or type(sha256) is not str
                    or (sha256 and (len(sha256) != 64
                        or any(c not in '0123456789abcdefABCDEF' for c in sha256)))):
                raise KoboAdeptError(
                    'Descripteurs de l’artefact ADEPT non qualifiés.',
                    code='kobo_adept_invalid_response',
                )
            loan = self._qualified_reference(value.get('loan_id'))
            name = self._qualified_reference(value.get('profile'))
            # ArtifactInput owns the validated destination, hash and format.
            # Do not also publish the entire CLI result in metadata: it may
            # include license XML, activation keys or signed delivery URLs.
            return DrmMutationResult(ok=True, operation='fulfill', artifact=ArtifactInput(
                path=Path(artifact['path']), format=media_format.casefold(),
                sha256=sha256.lower(),
            ), metadata={
                'runtime_result': {'ok': True, 'loan_id': loan, 'profile': name},
                'adept_loan_id': loan, 'adept_profile_name': name,
            })
        raise AttentionRequired('Opération DRM non publiée par Kobo-Adept.',code='kobo_adept_drm_mutation_unsupported')

    def download_request(self, request):
        if self.contract_version is None: self.contract()
        payload = dict(request)
        # Core 0.9.5 emits compatible schema-1 recipes; the v2 fields are identical here.
        payload['schema_version'] = 2
        self._require_download(payload)
        value = self.run_json(['download','request','-','--json'],payload=payload,
            timeout=int(self.config.get('download_timeout_seconds') or 900))
        artifact = value.get('artifact')
        if value.get('ok') is not True or not isinstance(artifact, dict) or not artifact.get('path'):
            raise KoboAdeptError('Artifact absent du résultat.',code='kobo_adept_invalid_response')
        if Path(artifact['path']).resolve() != Path(payload['destination']).resolve():
            raise KoboAdeptError('Adept a publié une destination différente de celle demandée.',code='kobo_adept_destination_mismatch')
        # Validation belongs to the final publisher/core; return all authoritative fields,
        # including profile and optional ADEPT loan_id, without inventing missing values.
        return value

    @staticmethod
    def _loan_profile(profile):
        # Caller resolves the stable profile ID to its current CLI name.
        # No implicit default and no discovery of unrelated identities.
        if (not isinstance(profile, str) or not profile or profile != profile.strip()
                or profile.startswith('-') or len(profile) > 256
                or any(ord(c) < 32 for c in profile)):
            raise AttentionRequired('Nom explicite du profil ADEPT requis.', code='adept_profile_required')
        return profile

    def _loan_inventory(self, profile, *, expired=False):
        name = self._loan_profile(profile)
        if self.contract_version is None:
            self.contract()
        operation = 'expired' if expired else 'list'
        # Reading the registry is independent of loan_return and fetch readiness.
        value = self.run_json(['loans', operation, '--profile', name, '--json'],
                              timeout=30, max_output_bytes=4*1024*1024)
        rows = value.get('loans')
        if not isinstance(rows, list) or len(rows) > 5000:
            raise KoboAdeptError('Inventaire des prêts ADEPT invalide.', code='kobo_adept_invalid_loan_inventory')
        loans = []
        seen = set()
        for row in rows:
            if (type(row) is not dict
                    or type(row.get('id')) is not str
                    or not 1 <= len(row['id']) <= 256
                    or row['id'] != row['id'].strip()
                    or row['id'].startswith('-')
                    or any(ord(char) < 32 or ord(char) == 127 for char in row['id'])
                    or row['id'] in seen
                    or any(type(row.get(k)) is not bool for k in ('expired', 'returned', 'token_present'))):
                raise KoboAdeptError('Entrée de prêt ADEPT invalide.', code='kobo_adept_invalid_loan_inventory')
            seen.add(row['id'])
            if expired and (not row['expired'] or row['returned']):
                raise KoboAdeptError('Inventaire expiré incohérent.', code='kobo_adept_invalid_loan_inventory')
            loan = {k: row[k] for k in ('id', 'expired', 'returned', 'token_present')}
            # Only these optional fields are consumed by Core inventory and
            # the explicit, user-selected loan binding UI. Do not forward
            # opaque vendor dictionaries in devices or incidental timestamps.
            # These can contain certificate paths, signed URLs and tokens.
            title = row.get('title')
            if (type(title) is str and 1 <= len(title) <= 512
                    and not any(ord(char) < 32 or ord(char) == 127 for char in title)):
                loan['title'] = title
            validity = row.get('validity')
            # An unknown/untyped validity is omitted, never interpreted as
            # a verified expiry date. Preserve only an ISO calendar date.
            if type(validity) is str and len(validity) == 10:
                try:
                    # Exact ISO8601 calendar date, no arbitrary vendor prose.
                    if date.fromisoformat(validity).isoformat() == validity:
                        loan['validity'] = validity
                except ValueError:
                    pass
            local_book = row.get('local_book')
            # Core needs the exact path for *local*, non-inferred artifact
            # matching. Opaque HTTP URLs and control characters are not file
            # paths and must not be passed to the loan choice UI.
            if (type(local_book) is str and 0 < len(local_book) <= 8192
                    and Path(local_book).is_absolute()
                    and '://' not in local_book
                    and not any(ord(char) < 32 or ord(char) == 127 for char in local_book)):
                loan['local_book'] = local_book
            loans.append(loan)
        self.last_run_diagnostic.update(loan_count=len(loans), inventory_valid=True)
        return {'loans': loans}

    def loans(self, profile):
        """Read tracked ADEPT loans for an explicit CLI profile name."""
        return self._loan_inventory(profile)

    def expired_loans(self, profile):
        """Read expired, not-returned loans. Never return one automatically."""
        return self._loan_inventory(profile, expired=True)

    def loan_inventory_readonly(self, profile_reference, *, consented=False, control=None):
        """Optional isolated inventory capability; Core owns binding/consent."""
        from cifereca_adept_readonly_inventory import read_inventory
        return read_inventory(self, profile_reference, consented=consented, control=control)

    def loan_inventories(self, profiles, *, expired=False):
        """Read only caller-designated candidates, retaining profile namespaces.

        Identity/origin selection and Artifact matching belong to Core. A
        partial response cannot establish global absence or a unique binding.
        """
        if not isinstance(profiles, (list, tuple)) or len(profiles) > 8:
            raise AttentionRequired('Liste explicite de huit profils ADEPT au maximum requise.', code='adept_profile_candidates_invalid')
        names = list(dict.fromkeys(self._loan_profile(p) for p in profiles))
        result = {'profiles': [], 'errors': [], 'complete': True}
        for name in names:
            try:
                inventory = self._loan_inventory(name, expired=expired)
                result['profiles'].append({'profile': name, 'loans': inventory['loans']})
            except UserFacingError as exc:
                result['complete'] = False
                # A provider error may include credentials, license XML,
                # device identity or signed URLs. safe_message(str(exc))
                # is heuristic redaction, not an export authorization.
                result['errors'].append({'profile': name, **_public_inventory_error(exc)})
        return result

    def probe_core(self):
        # Core/Diagnostics require capability flags, not raw vendor JSON.
        # #2845: measure two explicit Core probe calls independently, without
        # inferring shared account scope or preserving any private CLI argv.
        from cifereca_runtime_probe_safe import public_core_probe

        def elapsed_ms(start_ns):
            return max(0, min(600000, (time.perf_counter_ns() - start_ns) // 1000000))

        with _adept_caller("core_probe"):
            started = time.perf_counter_ns()
            contract = self.contract()
            contract_ms = elapsed_ms(started)
            started = time.perf_counter_ns()
            capabilities = self.capabilities()
            capabilities_ms = elapsed_ms(started)
        result = public_core_probe(
            contract, capabilities, bool(shutil.which(self.command[0])),
        )
        result["probe_timings_ms"] = {
            "contract": contract_ms, "capabilities": capabilities_ms,
        }
        return result

    def probe(self):
        return self.probe_core()
    @staticmethod
    def _profile_name(name):
        value=str(name or '').strip()
        if not value or value.startswith('-') or len(value)>128 or any(ord(ch)<32 for ch in value):
            raise AttentionRequired('Nom de profil ADEPT invalide.',code='adept_profile_name_invalid')
        return value

    @staticmethod
    def _profile_username(username):
        value=str(username or '').strip()
        if not value or '@' not in value or len(value)>320 or value.startswith('-') or any(ord(ch)<32 for ch in value):
            raise AttentionRequired('Adresse Adobe invalide.',code='adept_profile_username_invalid')
        return value

    @staticmethod
    def _profile_payload(value):
        if not isinstance(value,dict):
            return {}
        profile=value.get('profile')
        return profile if isinstance(profile,dict) else value

    @classmethod
    def _profile_ready(cls,value):
        profile=cls._profile_payload(value)
        provisioning=profile.get('provisioning') if isinstance(profile.get('provisioning'),dict) else {}
        return (profile.get('complete') is True and profile.get('fetch_capable') is True
                and provisioning.get('acsm_fetch_ready') is True)

    def profiles(self): return self.run_json(['profile','list','--json'],timeout=30)
    def profile(self,name): return self.run_json(['profile','show',self._profile_name(name),'--json'],timeout=30)

    def provision_profile(self,name,username):
        if not self._available('adept','profile_provisioning'):
            raise AttentionRequired('Provisioning de profil ADEPT indisponible.',code='adept_profile_provisioning_unsupported')
        profile=self._profile_name(name); adobe=self._profile_username(username)
        self.run_json(['profile','add',profile,'--username',adobe,'--json'],timeout=180)
        shown=self.profile(profile)
        if not self._profile_ready(shown):
            raise AttentionRequired('Le profil ADEPT a été créé mais n’est pas prêt pour les ACSM.',
                code='adept_profile_provisioning_incomplete')
        return shown

    def associate_profile(self,name,mount):
        if not self._available('adept','profile_provisioning'):
            raise AttentionRequired('Association de liseuse ADEPT indisponible.',code='adept_profile_association_unsupported')
        profile=self._profile_name(name)
        path=Path(str(mount or '')).expanduser()
        if not str(mount or '').strip() or not path.is_dir():
            raise AttentionRequired('Point de montage de liseuse invalide.',code='adept_mount_invalid')
        self.run_json(['profile','associate',profile,'--mount',str(path),'--json'],timeout=180)
        return self.profile(profile)

    def remove_profile(self,name):
        if not self._available('adept','profile_provisioning'):
            raise AttentionRequired('Suppression de profil ADEPT indisponible.',code='adept_profile_remove_unsupported')
        profile=self._profile_name(name)
        # Never use --force automatically: associated devices must stay protected.
        return self.run_json(['profile','remove',profile,'--json'],timeout=120)


class Plugin:
    def __init__(self): self.service = None
    def activate(self, context):
        self.service = KoboAdeptRuntime(context)
        with _adept_caller("activation"):
            self.service.contract()
            self.service.capabilities()
        return self.service
    def deactivate(self): self.service = None


PLUGIN = Plugin()
