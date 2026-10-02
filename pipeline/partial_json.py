# pipeline/partial_json.py
"""Read one string field out of a JSON object that is still streaming in.

Structured-output calls stream their JSON a few characters at a time. To show
Claude's text as it is written, we need the growing value of one top-level
string field (e.g. "script") from an incomplete document. This scanner walks
the top-level object, skipping nested values and strings (so a key name that
appears inside another value never matches), and decodes the field's string
up to the last complete character. An escape cut off at the end of the
buffer is left out until the rest of it arrives.
"""
from __future__ import annotations

_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_WS = " \t\r\n"


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in _WS:
        i += 1
    return i


def _decode_string(s: str, i: int) -> tuple[str, int, bool]:
    """Decode a JSON string whose opening quote is at s[i-1].
    Returns (text so far, index after the closing quote or len(s), closed)."""
    out: list[str] = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            return "".join(out), i + 1, True
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            break  # escape cut off
        e = s[i + 1]
        if e in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[e])
            i += 2
            continue
        if e == "u":
            if i + 6 > n:
                break
            try:
                code = int(s[i + 2:i + 6], 16)
            except ValueError:
                out.append(s[i:i + 6])
                i += 6
                continue
            if 0xD800 <= code <= 0xDBFF:
                # High surrogate: needs the low half (\uDC00-\uDFFF) to make a character.
                if i + 12 > n:
                    break
                if s[i + 6:i + 8] == "\\u":
                    try:
                        low = int(s[i + 8:i + 12], 16)
                    except ValueError:
                        low = -1
                    if 0xDC00 <= low <= 0xDFFF:
                        out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                        i += 12
                        continue
                out.append("�")
                i += 6
                continue
            out.append(chr(code))
            i += 6
            continue
        out.append(e)  # invalid escape: keep the character
        i += 2
    return "".join(out), n, False


def _skip_value(s: str, i: int) -> int | None:
    """Index after the JSON value starting at s[i], or None if it is incomplete."""
    n = len(s)
    if i >= n:
        return None
    c = s[i]
    if c == '"':
        _, j, closed = _decode_string(s, i + 1)
        return j if closed else None
    if c in "{[":
        depth = 0
        while i < n:
            c = s[i]
            if c == '"':
                _, i, closed = _decode_string(s, i + 1)
                if not closed:
                    return None
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None
    # number / true / false / null
    while i < n and s[i] not in ",}] \t\r\n":
        i += 1
    return i if i < n else None


def extract_string_field(partial: str, field: str) -> str | None:
    """Current value of top-level string `field` in partial JSON `partial`.

    None when the field has not started yet (or is not a string). Once its
    opening quote has arrived, returns the decoded text so far (possibly "").
    """
    s = partial
    i = _skip_ws(s, 0)
    if i >= len(s) or s[i] != "{":
        return None
    i += 1
    while True:
        i = _skip_ws(s, i)
        if i >= len(s):
            return None
        if s[i] == ",":
            i += 1
            continue
        if s[i] == "}":
            return None
        if s[i] != '"':
            return None
        key, i, closed = _decode_string(s, i + 1)
        if not closed:
            return None
        i = _skip_ws(s, i)
        if i >= len(s) or s[i] != ":":
            return None
        i = _skip_ws(s, i + 1)
        if i >= len(s):
            return None
        if key == field:
            if s[i] != '"':
                return None
            value, _, _ = _decode_string(s, i + 1)
            return value
        j = _skip_value(s, i)
        if j is None:
            return None
        i = j
