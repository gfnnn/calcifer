"""Parse WoW SavedVariables (``.lua``) into Python data — offline, no dependency.

WoW addons persist their state as Lua *data* tables (no functions, no
expressions) in ``WTF/Account/<acct>/SavedVariables/<Addon>.lua``. Each file is
a sequence of top-level assignments::

    SomeAddonDB = { ... }
    SomeAddon_Other = true

This reads exactly that data subset of Lua — nil / booleans / numbers / strings
(quoted and long-bracket ``[[ ]]``) / tables (array *and* keyed, 1-indexed like
Lua) — so a real addon dump parses with no third-party Lua runtime, fitting the
project's offline-first pipeline. It is deliberately strict about the value
grammar (only literals and tables, never code) and tolerant of whitespace,
comments, and trailing separators, so it never executes anything from an
uploaded file.

The two addon-specific mappers (``rclootcouncil``, ``grm``) sit on top of this
and turn the raw nested dict into domain rows.
"""

from __future__ import annotations

import re
from typing import Any

# A Lua table parses to a dict. Array parts use 1-based integer keys (as Lua
# does), so ``{ "a", "b" }`` becomes ``{1: "a", 2: "b"}`` and round-trips with
# explicit ``[1]=`` / ``[2]=`` keys.
LuaValue = Any

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Lua numbers: hex, or decimal with optional fraction/exponent. Leading sign is
# handled by the caller so it composes with unary minus on table values.
_NUMBER = re.compile(
    r"""0[xX][0-9a-fA-F]+            # hex integer
        | (?:\d+\.?\d* | \.\d+)      # 12 / 12. / 12.5 / .5
          (?:[eE][+-]?\d+)?          # optional exponent
    """,
    re.VERBOSE,
)
_KEYWORDS = {"nil": None, "true": True, "false": False}
# Lua 5.1 single-char string escapes (WoW runs 5.1). Unknown ``\x`` falls
# through to the literal character, which matches Lua's lenient behaviour.
_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n",
}


class LuaParseError(ValueError):
    """Raised when the input is not the SavedVariables data subset we accept."""


class _Parser:
    """A tiny recursive-descent reader over the SavedVariables data grammar."""

    __slots__ = ("text", "pos", "n")

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.n = len(text)

    # -- scanning helpers ----------------------------------------------------

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < self.n else ""

    def _error(self, msg: str) -> LuaParseError:
        return LuaParseError(f"{msg} at position {self.pos}")

    def _skip_trivia(self) -> None:
        """Skip whitespace and Lua comments (``-- line`` and ``--[[ block ]]``)."""
        while self.pos < self.n:
            c = self.text[self.pos]
            if c in " \t\r\n":
                self.pos += 1
            elif c == "-" and self.text.startswith("--", self.pos):
                self.pos += 2
                # A long-bracket comment: --[[ ... ]] / --[=[ ... ]=].
                if self._peek() == "[":
                    level = self._long_bracket_level()
                    if level is not None:
                        self._read_long_bracket(level)
                        continue
                # Otherwise a line comment.
                nl = self.text.find("\n", self.pos)
                self.pos = self.n if nl == -1 else nl + 1
            else:
                return

    def _long_bracket_level(self) -> int | None:
        """If positioned on ``[`` of a long bracket, return its ``=`` level."""
        i = self.pos
        if self.text[i:i + 1] != "[":
            return None
        j = i + 1
        while j < self.n and self.text[j] == "=":
            j += 1
        return j - (i + 1) if j < self.n and self.text[j] == "[" else None

    def _read_long_bracket(self, level: int) -> str:
        """Read a ``[[ ... ]]`` / ``[=[ ... ]=]`` long string body, sans markers."""
        self.pos += level + 2  # skip the opening [==[
        # Lua drops a single immediate newline after the opening bracket.
        if self._peek() == "\r":
            self.pos += 1
        if self._peek() == "\n":
            self.pos += 1
        close = "]" + "=" * level + "]"
        end = self.text.find(close, self.pos)
        if end == -1:
            raise self._error("unterminated long bracket")
        body = self.text[self.pos:end]
        self.pos = end + len(close)
        return body

    # -- value grammar -------------------------------------------------------

    def parse_value(self) -> LuaValue:
        self._skip_trivia()
        c = self._peek()
        if c == "":
            raise self._error("expected a value")
        if c == "{":
            return self._parse_table()
        if c in "\"'":
            return self._parse_quoted_string()
        if c == "[" and self._long_bracket_level() is not None:
            return self._read_long_bracket(self._long_bracket_level())  # type: ignore[arg-type]
        if c == "-" or c.isdigit() or c == ".":
            return self._parse_number()
        # nil / true / false (and reject anything else — never a bare global).
        m = _IDENT.match(self.text, self.pos)
        if m and m.group() in _KEYWORDS:
            self.pos = m.end()
            return _KEYWORDS[m.group()]
        raise self._error(f"unexpected token {c!r}")

    def _parse_number(self) -> int | float:
        start = self.pos
        if self._peek() == "-":
            self.pos += 1
        m = _NUMBER.match(self.text, self.pos)
        if not m:
            raise self._error("malformed number")
        self.pos = m.end()
        raw = self.text[start:self.pos]
        body = m.group()
        if body[:2].lower() == "0x":
            return int(raw, 16)
        if any(ch in body for ch in ".eE"):
            return float(raw)
        return int(raw)

    def _parse_quoted_string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out: list[str] = []
        while self.pos < self.n:
            c = self.text[self.pos]
            if c == quote:
                self.pos += 1
                return "".join(out)
            if c == "\\":
                self.pos += 1
                out.append(self._read_escape())
                continue
            out.append(c)
            self.pos += 1
        raise self._error("unterminated string")

    def _read_escape(self) -> str:
        c = self._peek()
        if c == "":
            raise self._error("dangling escape")
        if c.isdigit():  # \ddd decimal byte (1-3 digits)
            digits = ""
            while len(digits) < 3 and self._peek().isdigit():
                digits += self.text[self.pos]
                self.pos += 1
            return chr(int(digits) & 0xFF)
        if c == "x":  # \xHH (Lua 5.2+, but tolerate it)
            self.pos += 1
            hexs = self.text[self.pos:self.pos + 2]
            if len(hexs) == 2 and all(h in "0123456789abcdefABCDEF" for h in hexs):
                self.pos += 2
                return chr(int(hexs, 16))
            return "x"
        self.pos += 1
        return _ESCAPES.get(c, c)

    def _parse_table(self) -> dict[LuaValue, LuaValue]:
        self.pos += 1  # consume '{'
        result: dict[LuaValue, LuaValue] = {}
        array_index = 1
        while True:
            self._skip_trivia()
            c = self._peek()
            if c == "":
                raise self._error("unterminated table")
            if c == "}":
                self.pos += 1
                return result
            key, value = self._parse_field()
            if key is _POSITIONAL:
                result[array_index] = value
                array_index += 1
            else:
                result[key] = value
            self._skip_trivia()
            if self._peek() in ",;":
                self.pos += 1

    def _parse_field(self) -> tuple[LuaValue, LuaValue]:
        """Return ``(key, value)`` for one table entry; key is ``_POSITIONAL``
        for an array element."""
        c = self._peek()
        # [keyexpr] = value
        if c == "[" and self._long_bracket_level() is None:
            self.pos += 1
            key = self.parse_value()
            self._skip_trivia()
            if self._peek() != "]":
                raise self._error("expected ']' after table key")
            self.pos += 1
            self._expect_eq()
            return key, self.parse_value()
        # name = value  (bare identifier key, never a keyword)
        bare = self._try_bare_key()
        if bare is not None:
            return bare, self.parse_value()
        # positional array element
        return _POSITIONAL, self.parse_value()

    def _try_bare_key(self) -> str | None:
        """Consume ``ident =`` and return ``ident`` if present; else don't move."""
        m = _IDENT.match(self.text, self.pos)
        if not m or m.group() in _KEYWORDS:
            return None
        after = m.end()
        j = after
        while j < self.n and self.text[j] in " \t\r\n":
            j += 1
        # Require a single '=' (not '==', which would be a comparison, never
        # valid in the data subset but worth not mis-reading).
        if j < self.n and self.text[j] == "=" and self.text[j:j + 2] != "==":
            self.pos = j + 1
            return m.group()
        return None

    def _expect_eq(self) -> None:
        self._skip_trivia()
        if self._peek() != "=":
            raise self._error("expected '='")
        self.pos += 1


# Sentinel marking an array (positional) entry, distinct from any real Lua key.
_POSITIONAL = object()


def _dump_string(s: str) -> str:
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _dump_value(value: LuaValue, indent: int) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):  # before int — bools are ints in Python
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _dump_string(value)
    if isinstance(value, dict):
        return _dump_table(value, indent)
    if isinstance(value, (list, tuple)):
        return _dump_table({i + 1: v for i, v in enumerate(value)}, indent)
    raise TypeError(f"cannot serialise {type(value).__name__} to Lua")


def _dump_table(table: dict, indent: int) -> str:
    if not table:
        return "{}"
    pad = "    " * (indent + 1)
    close = "    " * indent
    rows = []
    for key, value in table.items():
        bracket = f"[{key}]" if isinstance(key, int) else f"[{_dump_string(str(key))}]"
        rows.append(f"{pad}{bracket} = {_dump_value(value, indent + 1)},")
    return "{\n" + "\n".join(rows) + f"\n{close}}}"


def dump(data: dict[str, LuaValue]) -> str:
    """Serialise a parsed mapping back into a SavedVariables Lua file.

    The inverse of :func:`parse_savedvariables` over the value types it produces
    (nested tables, strings, numbers, booleans, nil). Tables are always written
    in explicit ``[key] = value`` form, which round-trips faithfully — used to
    write a sanitised fixture after redaction.
    """
    return "".join(f"{name} = {_dump_value(value, 0)}\n" for name, value in data.items())


def parse_lua_value(text: str) -> LuaValue:
    """Parse a single Lua data value (table/string/number/bool/nil)."""
    p = _Parser(text)
    value = p.parse_value()
    p._skip_trivia()
    if p.pos != p.n:
        raise p._error("trailing data after value")
    return value


def parse_savedvariables(text: str) -> dict[str, LuaValue]:
    """Parse a whole SavedVariables file into ``{global_name: value}``.

    A SavedVariables file is a flat list of ``Name = <value>`` assignments. Any
    leading byte-order mark and comments are tolerated. Raises ``LuaParseError``
    on anything outside the accepted data subset.
    """
    if text and text[0] == "﻿":  # strip a UTF-8 BOM if present
        text = text[1:]
    p = _Parser(text)
    out: dict[str, LuaValue] = {}
    while True:
        p._skip_trivia()
        if p.pos >= p.n:
            return out
        m = _IDENT.match(p.text, p.pos)
        if not m:
            raise p._error("expected a global name")
        name = m.group()
        p.pos = m.end()
        p._expect_eq()
        out[name] = p.parse_value()
