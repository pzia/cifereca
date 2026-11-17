"""Transport-free bounded JSON for catalogue adapters (#6590).

Reject ambiguous object members in wire input. Validate already decoded
JSON values separately, without serialization, recursion or copy. Neither
operation grants trust to a link, a record, a provider or an entitlement.
"""
from __future__ import annotations

import json
import math

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 50000
MAX_JSON_TEXT = 2 * 1024 * 1024


class CatalogueJSONError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogueJSONError("duplicate_member")
        result[key] = value
    return result


def _integer(value):
    if len(value) > 128:
        raise CatalogueJSONError("budget")
    return int(value)


def _float(value):
    if len(value) > 128:
        raise CatalogueJSONError("budget")
    result = float(value)
    if not math.isfinite(result):
        raise CatalogueJSONError("invalid_number")
    return result


def _constant(_value):
    raise CatalogueJSONError("invalid_number")


def _check_depth(text):
    # Preflight only: json.loads still validates syntax. Quoted brackets
    # and escaped quotes do not change nesting.
    depth = 0
    quoted = escaped = False
    for ch in text:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise CatalogueJSONError("budget")
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                raise CatalogueJSONError("malformed")


def validate_catalogue_json(value):
    """Accept only JSON builtins, count repeated references as separate nodes.

    Decoded adapters cannot prove byte size or duplicate member absence.
    Their input is bounded independently by depth, nodes and UTF-8 text.
    Cycles fail the depth budget; sharing an object does not waive the budget.
    """
    pending = [(value, 0)]
    nodes = text_bytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise CatalogueJSONError("budget")
        if type(item) is dict:
            if depth >= MAX_JSON_DEPTH or len(item) > MAX_JSON_NODES - nodes - len(pending):
                raise CatalogueJSONError("budget")
            for key, child in item.items():
                if type(key) is not str:
                    raise CatalogueJSONError("invalid_structure")
                if len(key) > MAX_JSON_TEXT - text_bytes:
                    raise CatalogueJSONError("budget")
                try:
                    text_bytes += len(key.encode("utf-8"))
                except UnicodeError:
                    raise CatalogueJSONError("invalid_text") from None
                if text_bytes > MAX_JSON_TEXT:
                    raise CatalogueJSONError("budget")
                pending.append((child, depth+1))
        elif type(item) is list:
            if depth >= MAX_JSON_DEPTH or len(item) > MAX_JSON_NODES - nodes - len(pending):
                raise CatalogueJSONError("budget")
            pending.extend((child, depth+1) for child in item)
        elif type(item) is str:
            if len(item) > MAX_JSON_TEXT - text_bytes:
                raise CatalogueJSONError("budget")
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeError:
                raise CatalogueJSONError("invalid_text") from None
            if text_bytes > MAX_JSON_TEXT:
                raise CatalogueJSONError("budget")
        elif type(item) is int:
            if item.bit_length() > 426:
                raise CatalogueJSONError("budget")
        elif type(item) is float:
            if not math.isfinite(item):
                raise CatalogueJSONError("invalid_number")
        elif item is not None and type(item) is not bool:
            raise CatalogueJSONError("invalid_structure")
    return value


def parse_bounded_catalogue_json(content):
    """Parse UTF-8 JSON bytes once; no IO or supplier-specific interpretation."""
    if type(content) is not bytes or not 1 <= len(content) <= MAX_JSON_BYTES:
        raise CatalogueJSONError("budget")
    failure = None
    try:
        text = content.decode("utf-8-sig")
        _check_depth(text)
        result = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer,
                            parse_float=_float, parse_constant=_constant)
        return validate_catalogue_json(result)
    except CatalogueJSONError as exc:
        failure = exc.code
    except UnicodeError:
        failure = "invalid_text"
    except (ValueError, OverflowError, RecursionError):
        failure = "malformed"
    # Leave the parser exception context behind, including source text.
    raise CatalogueJSONError(failure)
