"""A jq subset, so `--jq` works with no jq binary installed.

`gh` embeds gojq, and the output rules below were measured against it rather
than assumed:

    gh api repos/pkgforge/soar --jq '.missing'     -> no output at all
    gh api repos/pkgforge/soar --jq '1,2'          -> two lines
    gh api repos/pkgforge/soar --jq '"x"'          -> x          (strings raw)
    gh api repos/pkgforge/soar --jq '{z:1,a:2}'    -> {"a":2,"z":1}  (keys sorted)
    gh api repos/pkgforge/soar --jq '10/4'         -> 2.50       (top-level float, 2dp)
    gh api repos/pkgforge/soar --jq '4/2'          -> 2          (integral float)
    gh api repos/pkgforge/soar --jq '[1.5]'        -> [1.5]      (containers keep JSON)

So: a null result is dropped, a string prints raw, a top-level float prints
with two decimals unless it is integral, and everything else is compact JSON
with sorted object keys (gojq decodes into Go maps, which marshal sorted).

Anything this module cannot parse raises JqError, and the caller may then try
a real jq binary if one exists.
"""

from __future__ import annotations

import base64
import json
import math
import re
import urllib.parse

__all__ = ["JqError", "run"]


class JqError(Exception):
    """A parse or evaluation failure.

    `str(exc)` is the exact line `gh` writes to stderr: runtime errors are one
    bare line, parse errors are gojq's three-line block with the caret.
    """

    def __init__(self, text, pos=None, detail=None):
        super().__init__(text)
        self.text = text
        self.pos = pos
        self.detail = detail


class _ParseError(JqError):
    """Raised by the parser; formatted by run() into gojq's caret block."""


# ---------------------------------------------------------------- tokens

class _Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):
        return "_Token(%r,%r)" % (self.kind, self.value)


_KEYWORDS = {
    "if", "then", "elif", "else", "end", "try", "catch", "and", "or",
    "as", "reduce", "foreach", "label", "break", "def", "true", "false",
    "null", "import", "include", "module",
}

_TWO_CHAR = ("//", "==", "!=", "<=", ">=", "..", "|=", "+=", "-=", "*=", "/=", "//=")

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _tokenize(text):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == '"':
            value, i = _read_string(text, i)
            tokens.append(_Token("string", value, i))
            continue
        if ch == "$":
            match = _NAME_RE.match(text, i + 1)
            if not match:
                raise _ParseError("unexpected token", i)
            tokens.append(_Token("var", match.group(0), i))
            i = match.end()
            continue
        if ch == "@":
            match = _NAME_RE.match(text, i + 1)
            if not match:
                raise _ParseError("unexpected token", i)
            tokens.append(_Token("format", match.group(0), i))
            i = match.end()
            continue
        if ch.isdigit() or (ch == "-" and i + 1 < n and text[i + 1].isdigit()
                            and _chained_after_number(tokens)):
            match = _NUMBER_RE.match(text, i)
            if not match:
                raise _ParseError("unexpected token", i)
            raw = match.group(0)
            value = float(raw) if any(c in raw for c in ".eE") else int(raw)
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            tokens.append(_Token("number", value, i))
            i = match.end()
            continue
        match = _NAME_RE.match(text, i)
        if match:
            word = match.group(0)
            kind = "keyword" if word in _KEYWORDS else "name"
            tokens.append(_Token(kind, word, i))
            i = match.end()
            continue
        two = text[i:i + 3]
        if two == "//=":
            tokens.append(_Token("op", "//=", i))
            i += 3
            continue
        two = text[i:i + 2]
        if two in _TWO_CHAR:
            tokens.append(_Token("op", two, i))
            i += 2
            continue
        if ch in "[]{}(),:;.|+-*/%?<>=":
            tokens.append(_Token("op", ch, i))
            i += 1
            continue
        raise _ParseError("unexpected character %r" % ch, i)
    tokens.append(_Token("eof", None, n))
    return tokens


def _chained_after_number(tokens):
    """A `-` starts a negative literal only where an operand may begin.

    After a value (`a-900`, `1-2`, `"x"-1`) it is binary minus; after an
    operator or an opening bracket it is a sign. A bare name is a value.
    """
    if not tokens:
        return True
    last = tokens[-1]
    if last.kind in ("number", "string", "var", "format", "name"):
        return False
    if last.kind == "op" and last.value in (")", "]", "}"):
        return False
    if last.kind == "keyword" and last.value in ("true", "false", "null"):
        return False
    return True


_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
            "n": "\n", "r": "\r", "t": "\t"}


def _read_string(text, i):
    """Read a jq string, keeping \\( ... ) interpolations as markers."""
    i += 1
    out = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            return "".join(out), i + 1
        if ch == "\\":
            if i + 1 >= n:
                raise _ParseError("unexpected EOF", i)
            esc = text[i + 1]
            if esc in _ESCAPES:
                out.append(_ESCAPES[esc])
                i += 2
                continue
            if esc == "u":
                code = text[i + 2:i + 6]
                try:
                    out.append(chr(int(code, 16)))
                except ValueError:
                    raise JqError("bad \\u escape")
                i += 6
                continue
            if esc == "(":
                depth = 1
                j = i + 2
                while j < n and depth:
                    if text[j] == '"':
                        _, j = _read_string(text, j)
                        continue
                    if text[j] == "(":
                        depth += 1
                    elif text[j] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                if depth:
                    raise _ParseError("unexpected EOF", j)
                out.append("\u0000INTERP" + text[i + 2:j] + "\u0000")
                i = j + 1
                continue
            out.append(esc)
            i += 2
            continue
        out.append(ch)
        i += 1
    raise _ParseError("unexpected EOF", i)


# ---------------------------------------------------------------- parser

class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0

    # token helpers
    def peek(self, offset=0):
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def next(self):
        token = self.tokens[self.index]
        self.index += 1
        return token

    def at(self, kind, value=None):
        token = self.peek()
        return token.kind == kind and (value is None or token.value == value)

    def accept(self, kind, value=None):
        if self.at(kind, value):
            return self.next()
        return None

    def expect(self, kind, value=None):
        token = self.accept(kind, value)
        if token is None:
            got = self.peek()
            if got.kind == "eof":
                raise _ParseError("unexpected EOF", got.pos)
            raise _ParseError("unexpected token %r" % (got.value,), got.pos)
        return token

    # grammar
    def parse(self):
        expr = self.parse_pipe()
        if not self.at("eof"):
            got = self.peek()
            raise _ParseError("unexpected token %r" % (got.value,), got.pos)
        return expr

    def parse_pipe(self):
        expr = self.parse_comma()
        while True:
            if self.accept("op", "|"):
                right = self.parse_comma()
                expr = ("pipe", expr, right)
                continue
            if self.at("keyword", "as"):
                self.next()
                name = self.expect("var").value
                if not self.accept("op", "|"):
                    raise JqError("expected '|' after 'as'")
                body = self.parse_pipe()
                expr = ("as", expr, name, body)
                continue
            return expr

    def parse_comma(self):
        expr = self.parse_alt()
        while self.accept("op", ","):
            expr = ("comma", expr, self.parse_alt())
        return expr

    def parse_alt(self):
        expr = self.parse_or()
        while self.accept("op", "//"):
            expr = ("alt", expr, self.parse_or())
        return expr

    def parse_or(self):
        expr = self.parse_and()
        while self.accept("keyword", "or"):
            expr = ("or", expr, self.parse_and())
        return expr

    def parse_and(self):
        expr = self.parse_compare()
        while self.accept("keyword", "and"):
            expr = ("and", expr, self.parse_compare())
        return expr

    def parse_compare(self):
        expr = self.parse_add()
        while True:
            for op in ("==", "!=", "<=", ">=", "<", ">"):
                if self.accept("op", op):
                    expr = ("cmp", op, expr, self.parse_add())
                    break
            else:
                return expr

    def parse_add(self):
        expr = self.parse_mul()
        while True:
            if self.accept("op", "+"):
                expr = ("arith", "+", expr, self.parse_mul())
            elif self.accept("op", "-"):
                expr = ("arith", "-", expr, self.parse_mul())
            else:
                return expr

    def parse_mul(self):
        expr = self.parse_unary()
        while True:
            if self.accept("op", "*"):
                expr = ("arith", "*", expr, self.parse_unary())
            elif self.accept("op", "/"):
                expr = ("arith", "/", expr, self.parse_unary())
            elif self.accept("op", "%"):
                expr = ("arith", "%", expr, self.parse_unary())
            else:
                return expr

    def parse_unary(self):
        if self.accept("op", "-"):
            return ("neg", self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_term()
        while True:
            token = self.peek()
            if token.kind == "op" and token.value == ".":
                self.next()
                field = self.peek()
                if field.kind in ("name", "keyword"):
                    self.next()
                    expr = ("field", expr, field.value)
                    continue
                if field.kind == "string":
                    self.next()
                    expr = ("index", expr, ("const", field.value))
                    continue
                got = self.peek()
                raise _ParseError("unexpected token %r" % (got.value,), got.pos)
            if token.kind == "op" and token.value == "[":
                self.next()
                expr = self.parse_bracket(expr)
                continue
            if token.kind == "op" and token.value == "?":
                self.next()
                expr = ("try", expr, None)
                continue
            return expr

    def parse_bracket(self, target):
        if self.accept("op", "]"):
            return ("iterate", target)
        start = None
        if not self.at("op", ":"):
            start = self.parse_pipe()
        if self.accept("op", ":"):
            end = None
            if not self.at("op", "]"):
                end = self.parse_pipe()
            self.expect("op", "]")
            return ("slice", target, start, end)
        self.expect("op", "]")
        return ("index", target, start)

    def parse_term(self):
        token = self.peek()
        if token.kind == "eof":
            raise _ParseError("unexpected EOF", token.pos)
        if token.kind == "number":
            self.next()
            return ("const", token.value)
        if token.kind == "string":
            self.next()
            return _interpolated(token.value)
        if token.kind == "format":
            self.next()
            return ("format", token.value)
        if token.kind == "var":
            self.next()
            return ("var", token.value)
        if token.kind == "keyword":
            word = token.value
            if word == "true":
                self.next()
                return ("const", True)
            if word == "false":
                self.next()
                return ("const", False)
            if word == "null":
                self.next()
                return ("const", None)
            if word == "if":
                return self.parse_if()
            if word == "try":
                self.next()
                body = self.parse_postfix()
                handler = None
                if self.accept("keyword", "catch"):
                    handler = self.parse_postfix()
                return ("try", body, handler)
            if word in ("reduce", "foreach"):
                self.next()
                return self.parse_reduce(word)
            if word == "def":
                raise JqError("`def` is not supported by this jq subset")
            if word == "label":
                raise JqError("`label` is not supported by this jq subset")
            if word == "break":
                raise JqError("`break` is not supported by this jq subset")
        if token.kind == "op" and token.value == ".":
            self.next()
            nxt = self.peek()
            if nxt.kind == "op" and nxt.value in (".",):
                pass
            if nxt.kind in ("name", "keyword") and nxt.value not in _KEYWORDS:
                self.next()
                return ("field", ("const", _IDENTITY), nxt.value)
            if nxt.kind == "string":
                self.next()
                return ("index", ("const", _IDENTITY), ("const", nxt.value))
            return ("const", _IDENTITY)
        if token.kind == "op" and token.value == "..":
            self.next()
            return ("recurse",)
        if token.kind == "op" and token.value == "(":
            self.next()
            expr = self.parse_pipe()
            self.expect("op", ")")
            return expr
        if token.kind == "op" and token.value == "[":
            self.next()
            if self.accept("op", "]"):
                return ("array", None)
            inner = self.parse_pipe()
            self.expect("op", "]")
            return ("array", inner)
        if token.kind == "op" and token.value == "{":
            return self.parse_object()
        if token.kind == "name":
            self.next()
            return self.parse_call(token.value)
        raise _ParseError("unexpected token %r" % (token.value,), token.pos)

    def parse_call(self, name):
        args = []
        if self.at("op", "("):
            self.next()
            if not self.at("op", ")"):
                args.append(self.parse_pipe())
                while self.accept("op", ";"):
                    args.append(self.parse_pipe())
            self.expect("op", ")")
        return ("call", name, args)

    def parse_if(self):
        self.expect("keyword", "if")
        branches = []
        condition = self.parse_pipe()
        self.expect("keyword", "then")
        branches.append((condition, self.parse_pipe()))
        while self.accept("keyword", "elif"):
            condition = self.parse_pipe()
            self.expect("keyword", "then")
            branches.append((condition, self.parse_pipe()))
        fallback = None
        if self.accept("keyword", "else"):
            fallback = self.parse_pipe()
        self.expect("keyword", "end")
        return ("if", branches, fallback)

    def parse_reduce(self, word):
        name = self.expect("var").value
        self.expect("keyword", "as")
        source = self.parse_postfix()
        self.expect("op", "(")
        if word == "reduce":
            init = self.parse_pipe()
            self.expect("op", ";")
            update = self.parse_pipe()
            self.expect("op", ")")
            return ("reduce", source, name, init, update)
        init = self.parse_pipe()
        self.expect("op", ";")
        update = self.parse_pipe()
        extract = None
        if self.accept("op", ";"):
            extract = self.parse_pipe()
        self.expect("op", ")")
        return ("foreach", source, name, init, update, extract)

    def parse_object(self):
        self.expect("op", "{")
        pairs = []
        if self.accept("op", "}"):
            return ("object", pairs)
        while True:
            if self.at("var"):
                name = self.next().value
                pairs.append((("const", name), ("var", name)))
            elif self.at("name") or self.at("keyword"):
                key = self.next().value
                if self.accept("op", ":"):
                    pairs.append((("const", key), self.parse_alt()))
                else:
                    pairs.append((("const", key), ("field", ("const", _IDENTITY), key)))
            elif self.at("string"):
                raw = self.next().value
                key_expr = _interpolated(raw)
                if self.accept("op", ":"):
                    pairs.append((key_expr, self.parse_alt()))
                else:
                    pairs.append((key_expr, ("index", ("const", _IDENTITY), key_expr)))
            elif self.accept("op", "("):
                key_expr = self.parse_pipe()
                self.expect("op", ")")
                self.expect("op", ":")
                pairs.append((key_expr, self.parse_alt()))
            else:
                got = self.peek()
                raise _ParseError("unexpected token %r" % (got.value,), got.pos)
            if self.accept("op", ","):
                continue
            self.expect("op", "}")
            return ("object", pairs)


class _Identity:
    def __repr__(self):
        return "<identity>"


_IDENTITY = _Identity()


def _interpolated(raw):
    """A jq string literal: constant text plus \\( ... ) pieces."""
    if "\u0000INTERP" not in raw:
        return ("const", raw)
    parts = []
    buffer = []
    i = 0
    while i < len(raw):
        if raw.startswith("\u0000INTERP", i):
            if buffer:
                parts.append(("const", "".join(buffer)))
                buffer = []
            end = raw.index("\u0000", i + 7)
            source = raw[i + 7:end]
            parts.append(_Parser(_tokenize(source)).parse())
            i = end + 1
            continue
        buffer.append(raw[i])
        i += 1
    if buffer:
        parts.append(("const", "".join(buffer)))
    return ("interp", parts)


# ---------------------------------------------------------------- values

def _truthy(value):
    return not (value is None or value is False)


def _type_of(value):
    if value is None:
        return "null"
    if value is True or value is False:
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


_ORDER = {"null": 0, "boolean": 1, "number": 2, "string": 3, "array": 4, "object": 5}


def _sort_key(value):
    kind = _type_of(value)
    if kind == "boolean":
        return (1, 0 if value is False else 1)
    if kind == "number":
        return (2, value)
    if kind == "string":
        return (3, value)
    if kind == "array":
        return (4, [_sort_key(item) for item in value])
    if kind == "object":
        return (5, [(key, _sort_key(value[key])) for key in sorted(value)])
    return (0, 0)


def _preview(value):
    """gojq truncates embedded values at 25 bytes, appends " ...", and keeps
    the container's closing bracket: (["a","b" ...]) and ({"k":1 ...})."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return _number_out(value)
    text = to_json(value)
    if len(text) <= 25:
        return text
    closing = "]" if isinstance(value, list) else "}"
    return text[:25] + " ..." + closing


def _wrong_type(expected, value):
    article = "an" if expected in ("object", "array") else "a"
    return "expected %s %s but got: %s (%s)" % (
        article, expected, _type_of(value), _preview(value))


def _not_applied(fn, value):
    return "%s cannot be applied to: %s (%s)" % (fn, _type_of(value), _preview(value))


def _arg_not_applied(fn, arg, value):
    return "%s(%s) cannot be applied to: %s (%s)" % (
        fn, to_json(arg), _type_of(value), _preview(value))


def _cannot(op, left, right):
    return "cannot %s: %s (%s) and %s (%s)" % (
        op, _type_of(left), _preview(left), _type_of(right), _preview(right))


def _number_out(value):
    """Top-level float formatting, matched to gh: two decimals, or an int."""
    if isinstance(value, int):
        return str(value)
    if value != value or value in (float("inf"), float("-inf")):
        return "null"
    if float(value).is_integer():
        return str(int(value))
    return "%.2f" % value


class _Encoder:
    """Compact JSON with sorted keys, the way gojq's Go maps marshal."""

    def encode(self, value):
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return "null"
            if value.is_integer():
                return str(int(value))
            return repr(value)
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            return "[" + ",".join(self.encode(item) for item in value) + "]"
        if isinstance(value, dict):
            return "{" + ",".join(
                json.dumps(str(key), ensure_ascii=False) + ":" + self.encode(value[key])
                for key in sorted(value)) + "}"
        raise JqError("cannot serialise %s" % _type_of(value))


_ENCODER = _Encoder()


def to_json(value):
    return _ENCODER.encode(value)


def to_text(value):
    """jq's tostring."""
    if isinstance(value, str):
        return value
    return to_json(value)


# ---------------------------------------------------------------- runtime

class _Runtime:
    """Evaluate an AST against an input value, yielding results."""

    def __init__(self):
        self.vars = {}

    def eval(self, node, value):
        kind = node[0]
        handler = getattr(self, "_eval_" + kind, None)
        if handler is None:
            raise JqError("unsupported expression %r" % (kind,))
        return handler(node, value)

    # -- basics
    def _eval_const(self, node, value):
        if node[1] is _IDENTITY:
            yield value
        else:
            yield node[1]

    def _eval_field(self, node, value):
        base = self.eval(node[1], value)
        key = node[2]
        for item in base:
            yield _index_value(item, key)

    def _eval_index(self, node, value):
        base = self.eval(node[1], value)
        for item in base:
            for index in self.eval(node[2], value):
                yield _index_value(item, index)

    def _eval_slice(self, node, value):
        for item in self.eval(node[1], value):
            starts = [None] if node[2] is None else list(self.eval(node[2], value))
            ends = [None] if node[3] is None else list(self.eval(node[3], value))
            for start in starts:
                for end in ends:
                    yield _slice_value(item, start, end)

    def _eval_iterate(self, node, value):
        for item in self.eval(node[1], value):
            if item is None:
                raise JqError("cannot iterate over: null (null)")
            if isinstance(item, list):
                for entry in item:
                    yield entry
            elif isinstance(item, dict):
                for key in sorted(item):
                    yield item[key]
            else:
                raise JqError("cannot iterate over: %s (%s)"
                              % (_type_of(item), _preview(item)))

    def _eval_pipe(self, node, value):
        for left in self.eval(node[1], value):
            for right in self.eval(node[2], left):
                yield right

    def _eval_comma(self, node, value):
        for item in self.eval(node[1], value):
            yield item
        for item in self.eval(node[2], value):
            yield item

    def _eval_array(self, node, value):
        if node[1] is None:
            yield []
            return
        yield [item for item in self.eval(node[1], value)]

    def _eval_object(self, node, value):
        out = {}
        for key_expr, value_expr in node[1]:
            for key in self.eval(key_expr, value):
                for item in self.eval(value_expr, value):
                    out[_key_text(key)] = item
        yield out

    def _eval_interp(self, node, value):
        parts = []
        for part in node[1]:
            if part[0] == "const":
                parts.append(part[1])
            else:
                for item in self.eval(part, value):
                    parts.append(to_text(item))
        yield "".join(parts)

    def _eval_var(self, node, value):
        name = node[1]
        if name not in self.vars:
            raise JqError("%s is not defined" % name)
        yield self.vars[name]

    def _eval_as(self, node, value):
        for item in self.eval(node[1], value):
            saved = self.vars.get(node[2], _MISSING)
            self.vars[node[2]] = item
            try:
                for out in self.eval(node[3], value):
                    yield out
            finally:
                if saved is _MISSING:
                    self.vars.pop(node[2], None)
                else:
                    self.vars[node[2]] = saved

    def _eval_if(self, node, value):
        for condition, body in node[1]:
            matched = False
            for item in self.eval(condition, value):
                matched = True
                if _truthy(item):
                    for out in self.eval(body, value):
                        yield out
                    return
            if not matched:
                continue
        if node[2] is not None:
            for out in self.eval(node[2], value):
                yield out

    def _eval_try(self, node, value):
        try:
            for item in self.eval(node[1], value):
                yield item
        except JqError:
            if node[2] is not None:
                for item in self.eval(node[2], value):
                    yield item

    def _eval_neg(self, node, value):
        for item in self.eval(node[1], value):
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                raise JqError("%s (%s) cannot be negated" % (_type_of(item), _preview(item)))
            yield -item

    def _eval_and(self, node, value):
        for left in self.eval(node[1], value):
            if not _truthy(left):
                yield False
                continue
            for right in self.eval(node[2], value):
                yield _truthy(right)

    def _eval_or(self, node, value):
        for left in self.eval(node[1], value):
            if _truthy(left):
                yield True
                continue
            for right in self.eval(node[2], value):
                yield _truthy(right)

    def _eval_alt(self, node, value):
        found = False
        try:
            for item in self.eval(node[1], value):
                if _truthy(item):
                    found = True
                    yield item
        except JqError:
            pass
        if not found:
            for item in self.eval(node[2], value):
                yield item

    def _eval_cmp(self, node, value):
        op = node[1]
        for left in self.eval(node[2], value):
            for right in self.eval(node[3], value):
                yield _compare(op, left, right)

    def _eval_arith(self, node, value):
        op = node[1]
        for left in self.eval(node[2], value):
            for right in self.eval(node[3], value):
                yield _arith(op, left, right)

    def _eval_recurse(self, node, value):
        for item in _recurse(value):
            yield item

    def _eval_reduce(self, node, value):
        source, name, init, update = node[1], node[2], node[3], node[4]
        for seed in self.eval(init, value):
            accumulator = seed
            for item in self.eval(source, value):
                saved = self.vars.get(name, _MISSING)
                self.vars[name] = item
                try:
                    for updated in self.eval(update, accumulator):
                        accumulator = updated
                finally:
                    if saved is _MISSING:
                        self.vars.pop(name, None)
                    else:
                        self.vars[name] = saved
            yield accumulator

    def _eval_foreach(self, node, value):
        source, name, init, update, extract = node[1:6]
        for seed in self.eval(init, value):
            accumulator = seed
            for item in self.eval(source, value):
                saved = self.vars.get(name, _MISSING)
                self.vars[name] = item
                try:
                    for updated in self.eval(update, accumulator):
                        accumulator = updated
                        if extract is None:
                            yield accumulator
                        else:
                            for out in self.eval(extract, accumulator):
                                yield out
                finally:
                    if saved is _MISSING:
                        self.vars.pop(name, None)
                    else:
                        self.vars[name] = saved

    def _eval_format(self, node, value):
        yield _format(node[1], value)

    # -- calls
    def _eval_call(self, node, value):
        name, args = node[1], node[2]
        handler = _BUILTINS.get((name, len(args)))
        if handler is None:
            raise JqError("function not defined: %s/%d" % (name, len(args)))
        for item in handler(self, args, value):
            yield item


_MISSING = object()


def _key_text(key):
    if isinstance(key, str):
        return key
    if isinstance(key, bool):
        return "true" if key else "false"
    if key is None:
        return "null"
    if isinstance(key, (int, float)):
        return _number_out(key)
    raise JqError("cannot use %s as an object key" % _type_of(key))


def _index_value(item, key):
    if item is None:
        return None
    is_text = isinstance(key, str)
    is_num = isinstance(key, (int, float)) and not isinstance(key, bool)
    if is_text or key is True or key is False:
        if isinstance(item, dict):
            return item.get(key)
        raise JqError(_wrong_type("object", item))
    if is_num:
        if isinstance(item, (list, str)):
            index = int(key)
            if index < 0:
                index += len(item)
            return item[index] if 0 <= index < len(item) else None
        raise JqError(_wrong_type("array", item))
    # an array or object used as an index: gojq answers with the value's type
    raise JqError(_wrong_type("array", item))


def _slice_value(item, start, end):
    if item is None:
        return None
    if not isinstance(item, (list, str)):
        raise JqError(_wrong_type("array", item))
    begin = None if start is None else int(start)
    finish = None if end is None else int(end)
    return item[begin:finish]


def _recurse(value):
    yield value
    if isinstance(value, dict):
        for key in value:
            for item in _recurse(value[key]):
                yield item
    elif isinstance(value, list):
        for entry in value:
            for item in _recurse(entry):
                yield item


def _compare(op, left, right):
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    lt = _sort_key(left) < _sort_key(right)
    if op == "<":
        return lt
    if op == "<=":
        return lt or left == right
    gt = _sort_key(left) > _sort_key(right)
    if op == ">":
        return gt
    return gt or left == right


def _arith(op, left, right):
    if op == "+":
        if left is None:
            return right
        if right is None:
            return left
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
                and not isinstance(left, bool) and not isinstance(right, bool):
            return _number(left + right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        if isinstance(left, dict) and isinstance(right, dict):
            merged = dict(left)
            merged.update(right)
            return merged
        raise JqError(_cannot("add", left, right))
    if op == "-":
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
                and not isinstance(left, bool) and not isinstance(right, bool):
            return _number(left - right)
        if isinstance(left, list) and isinstance(right, list):
            return [item for item in left if item not in right]
        raise JqError(_cannot("subtract", left, right))
    if op == "*":
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
                and not isinstance(left, bool) and not isinstance(right, bool):
            return _number(left * right)
        # gojq truncates the repeat count toward zero, from either side
        if isinstance(left, str) and isinstance(right, (int, float)) and not isinstance(right, bool):
            return left * max(0, int(right))
        if isinstance(right, str) and isinstance(left, (int, float)) and not isinstance(left, bool):
            return right * max(0, int(left))
        if isinstance(left, dict) and isinstance(right, dict):
            merged = dict(left)
            for key, item in right.items():
                merged[key] = _merge_deep(merged[key], item) if key in merged else item
            return merged
        raise JqError(_cannot("multiply", left, right))
    if op == "/":
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
                and not isinstance(left, bool) and not isinstance(right, bool):
            if right == 0:
                raise JqError("number (%s) and number (0) cannot be divided because the divisor is zero" % _preview(left))
            return _number(left / right)
        if isinstance(left, str) and isinstance(right, str):
            return left.split(right)
        raise JqError(_cannot("divide", left, right))
    if op == "%":
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
                and not isinstance(left, bool) and not isinstance(right, bool):
            if right == 0:
                raise JqError("cannot modulo: number (%s) and number (0)" % _preview(left))
            return _number(left % right)
        raise JqError(_cannot("modulo", left, right))
    raise JqError("unsupported operator %r" % op)


def _merge_deep(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        out = dict(left)
        for key, item in right.items():
            out[key] = _merge_deep(out[key], item) if key in out else item
        return out
    return right


def _number(value):
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return value
    return value


def _format(name, value):
    if name == "json":
        return to_json(value)
    if name == "text":
        return to_text(value)
    if name == "html":
        text = to_text(value)
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;"))
    if name == "uri":
        return urllib.parse.quote(to_text(value), safe="-_.~")
    if name == "base64":
        return base64.b64encode(to_text(value).encode()).decode()
    if name == "base64d":
        try:
            return base64.b64decode(to_text(value)).decode("utf-8", "replace")
        except Exception:
            raise JqError("base64d: invalid input")
    if name == "csv":
        if not isinstance(value, list):
            raise JqError(_not_applied("@csv", value))
        return ",".join(_csv_field(item) for item in value)
    if name == "tsv":
        if not isinstance(value, list):
            raise JqError(_not_applied("@tsv", value))
        return "\t".join(_tsv_field(item) for item in value)
    if name == "sh":
        if isinstance(value, list):
            return " ".join(_sh_field(item) for item in value)
        return _sh_field(value)
    raise JqError("unknown format @%s" % name)


def _csv_field(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number_out(value)
    text = to_text(value)
    text = text.replace('"', '""')
    return '"%s"' % text


def _tsv_field(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number_out(value)
    return (to_text(value).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\n", "\\n").replace("\r", "\\r"))


def _sh_field(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number_out(value)
    return "'" + to_text(value).replace("'", "'\\''") + "'"


# ---------------------------------------------------------------- builtins

def _simple(fn):
    def handler(runtime, args, value):
        yield fn(runtime, value)
    return handler


def _arg_values(runtime, expr, value):
    return list(runtime.eval(expr, value))


def _b_length(runtime, args, value):
    if value is None:
        yield 0
    elif isinstance(value, str):
        yield len(value)
    elif isinstance(value, (list, dict)):
        yield len(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield abs(value)
    else:
        raise JqError(_not_applied("length", value))


def _b_keys(runtime, args, value):
    if isinstance(value, dict):
        yield sorted(value)
    elif isinstance(value, list):
        yield list(range(len(value)))
    else:
        raise JqError(_not_applied("keys", value))


def _b_keys_unsorted(runtime, args, value):
    if isinstance(value, dict):
        yield list(value)
    elif isinstance(value, list):
        yield list(range(len(value)))
    else:
        raise JqError(_not_applied("keys_unsorted", value))


def _b_values(runtime, args, value):
    if isinstance(value, dict):
        yield list(value.values())
    elif isinstance(value, list):
        yield value
    else:
        raise JqError(_not_applied("values", value))


def _b_has(runtime, args, value):
    for key in _arg_values(runtime, args[0], value):
        if isinstance(value, dict):
            if not isinstance(key, str):
                raise JqError(_arg_not_applied("has", key, value))
            yield key in value
        elif isinstance(value, list):
            if not isinstance(key, (int, float)) or isinstance(key, bool):
                raise JqError(_arg_not_applied("has", key, value))
            yield 0 <= int(key) < len(value)
        else:
            raise JqError(_arg_not_applied("has", key, value))


def _b_map(runtime, args, value):
    if value is None:
        yield None
        return
    if not isinstance(value, (list, dict)):
        raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))
    items = value if isinstance(value, list) else list(value.values())
    out = []
    for item in items:
        results = list(runtime.eval(args[0], item))
        out.extend(results)
    yield out


def _b_map_values(runtime, args, value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            results = list(runtime.eval(args[0], item))
            if results:
                out[key] = results[0]
        yield out
    elif isinstance(value, list):
        out = []
        for item in value:
            results = list(runtime.eval(args[0], item))
            if results:
                out.append(results[0])
        yield out
    else:
        raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))


def _b_select(runtime, args, value):
    for item in runtime.eval(args[0], value):
        if _truthy(item):
            yield value
            return


def _b_type(runtime, args, value):
    yield _type_of(value)


def _b_tostring(runtime, args, value):
    yield to_text(value)


def _b_tonumber(runtime, args, value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        yield value
        return
    if isinstance(value, str):
        try:
            yield int(value)
            return
        except ValueError:
            try:
                yield float(value)
                return
            except ValueError:
                pass
    raise JqError("tonumber cannot be applied to %s: invalid number" % to_json(value))


def _b_tojson(runtime, args, value):
    yield to_json(value)


def _b_fromjson(runtime, args, value):
    if not isinstance(value, str):
        raise JqError("fromjson needs a string")
    try:
        yield json.loads(value)
    except ValueError as exc:
        raise JqError("invalid JSON text: %s" % exc)


def _b_sort(runtime, args, value):
    if not isinstance(value, list):
        raise JqError(_not_applied("sort", value))
    yield sorted(value, key=_sort_key)


def _b_sort_by(runtime, args, value):
    if not isinstance(value, list):
        raise JqError(_not_applied("sort_by", value))
    keyed = []
    for item in value:
        keys = list(runtime.eval(args[0], item))
        keyed.append((_sort_key(keys[0]) if keys else (0, 0), item))
    keyed.sort(key=lambda pair: pair[0])
    yield [item for _, item in keyed]


def _b_group_by(runtime, args, value):
    if not isinstance(value, list):
        raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))
    groups = []
    for item in value:
        keys = list(runtime.eval(args[0], item))
        key = _sort_key(keys[0]) if keys else (0, 0)
        for group in groups:
            if group[0] == key:
                group[1].append(item)
                break
        else:
            groups.append((key, [item]))
    groups.sort(key=lambda pair: pair[0])
    yield [items for _, items in groups]


def _b_unique(runtime, args, value):
    if not isinstance(value, list):
        raise JqError(_not_applied("unique", value))
    seen = []
    out = []
    for item in value:
        key = to_json(item)
        if key not in seen:
            seen.append(key)
            out.append(item)
    yield out


def _b_unique_by(runtime, args, value):
    if not isinstance(value, list):
        raise JqError(_not_applied("unique_by", value))
    seen = []
    out = []
    for item in value:
        keys = list(runtime.eval(args[0], item))
        key = to_json(keys[0]) if keys else "null"
        if key not in seen:
            seen.append(key)
            out.append(item)
    yield out


def _minmax(runtime, args, value, biggest):
    if not isinstance(value, list):
        raise JqError(_not_applied("min" if not biggest else "max", value))
    if not value:
        yield None
        return
    best = value[0]
    for item in value[1:]:
        if (_sort_key(item) > _sort_key(best)) == biggest:
            best = item
    yield best


def _minmax_by(runtime, args, value, biggest):
    if not isinstance(value, list):
        raise JqError(_not_applied("min_by" if not biggest else "max_by", value))
    if not value:
        yield None
        return
    best = value[0]
    best_key = _sort_key(list(runtime.eval(args[0], best))[0])
    for item in value[1:]:
        key = _sort_key(list(runtime.eval(args[0], item))[0])
        if (key > best_key) == biggest:
            best, best_key = item, key
    yield best


def _b_add(runtime, args, value):
    if value is None:
        yield None
        return
    if not isinstance(value, list):
        raise JqError(_not_applied("add", value))
    if not value:
        yield None
        return
    total = value[0]
    for item in value[1:]:
        total = _arith("+", total, item)
    yield total


def _b_any(runtime, args, value):
    if args:
        if not isinstance(value, list):
            raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))
        for item in value:
            if any(_truthy(x) for x in runtime.eval(args[0], item)):
                yield True
                return
        yield False
        return
    if isinstance(value, list):
        yield any(_truthy(item) for item in value)
    else:
        raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))


def _b_all(runtime, args, value):
    if args:
        if not isinstance(value, list):
            raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))
        for item in value:
            if not any(_truthy(x) for x in runtime.eval(args[0], item)):
                yield False
                return
        yield True
        return
    if isinstance(value, list):
        yield all(_truthy(item) for item in value)
    else:
        raise JqError("cannot iterate over: %s (%s)" % (_type_of(value), _preview(value)))


def _b_flatten(runtime, args, value):
    if not isinstance(value, list):
        raise JqError(_not_applied("flatten", value))
    depth = None
    if args:
        depth = list(runtime.eval(args[0], value))[0]
    yield _flatten(value, depth)


def _flatten(value, depth):
    out = []
    for item in value:
        if isinstance(item, list) and (depth is None or depth > 0):
            out.extend(_flatten(item, None if depth is None else depth - 1))
        else:
            out.append(item)
    return out


def _b_reverse(runtime, args, value):
    if isinstance(value, (list, str)):
        yield value[::-1]
    else:
        raise JqError(_not_applied("reverse", value))


def _b_range(runtime, args, value):
    if len(args) == 1:
        start, end = 0, list(runtime.eval(args[0], value))[0]
    else:
        start = list(runtime.eval(args[0], value))[0]
        end = list(runtime.eval(args[1], value))[0]
    for item in range(int(start), int(end)):
        yield item


def _b_empty(runtime, args, value):
    return
    yield  # pragma: no cover


def _b_error(runtime, args, value):
    message = value
    if args:
        message = list(runtime.eval(args[0], value))[0]
    if isinstance(message, str):
        raise JqError("error: " + message)
    raise JqError("error: " + to_json(message))
    yield  # pragma: no cover


def _b_not(runtime, args, value):
    yield not _truthy(value)


def _b_first(runtime, args, value):
    if args:
        for item in runtime.eval(args[0], value):
            yield item
            return
        return
    if isinstance(value, (list, str)):
        yield value[0] if value else None
    else:
        raise JqError(_wrong_type("array", value))


def _b_last(runtime, args, value):
    if args:
        last = None
        found = False
        for item in runtime.eval(args[0], value):
            last, found = item, True
        if found:
            yield last
        return
    if isinstance(value, (list, str)):
        yield value[-1] if value else None
    else:
        raise JqError(_wrong_type("array", value))


def _b_nth(runtime, args, value):
    index = list(runtime.eval(args[0], value))[0]
    if len(args) > 1:
        for position, item in enumerate(runtime.eval(args[1], value)):
            if position == index:
                yield item
                return
        return
    if isinstance(value, list):
        yield value[int(index)] if 0 <= int(index) < len(value) else None
    else:
        raise JqError("cannot index %s" % _type_of(value))


def _b_limit(runtime, args, value):
    count = int(list(runtime.eval(args[0], value))[0])
    if count <= 0:
        return
    seen = 0
    for item in runtime.eval(args[1], value):
        yield item
        seen += 1
        if seen >= count:
            return


def _b_join(runtime, args, value):
    separator = list(runtime.eval(args[0], value))[0]
    if not isinstance(value, list):
        raise JqError(_not_applied("join", value))
    out = None
    for item in value:
        piece = "" if item is None else to_text(item)
        if out is None:
            out = piece
        else:
            out = _arith("+", out, separator)
            out = _arith("+", out, piece)
    yield out


def _b_split(runtime, args, value):
    separator = list(runtime.eval(args[0], value))[0]
    if not isinstance(value, str):
        raise JqError(_not_applied("split", value))
    if not isinstance(separator, str):
        raise JqError(_not_applied("split", separator))
    yield value.split(separator)


def _b_startswith(runtime, args, value):
    prefix = list(runtime.eval(args[0], value))[0]
    if not isinstance(value, str) or not isinstance(prefix, str):
        raise JqError(_arg_not_applied("startswith", prefix, value))
    yield value.startswith(prefix)


def _b_endswith(runtime, args, value):
    suffix = list(runtime.eval(args[0], value))[0]
    if not isinstance(value, str) or not isinstance(suffix, str):
        raise JqError(_arg_not_applied("endswith", suffix, value))
    yield value.endswith(suffix)


def _b_ltrimstr(runtime, args, value):
    prefix = list(runtime.eval(args[0], value))[0]
    if not isinstance(prefix, str):
        raise JqError(_arg_not_applied("ltrimstr", prefix, value))
    if isinstance(value, str) and value.startswith(prefix):
        yield value[len(prefix):]
    else:
        yield value


def _b_rtrimstr(runtime, args, value):
    suffix = list(runtime.eval(args[0], value))[0]
    if not isinstance(suffix, str):
        raise JqError(_arg_not_applied("rtrimstr", suffix, value))
    if isinstance(value, str) and value.endswith(suffix):
        yield value[:-len(suffix)] if suffix else value
    else:
        yield value


def _b_contains(runtime, args, value):
    other = list(runtime.eval(args[0], value))[0]
    if not _compatible(value, other):
        raise JqError(_arg_not_applied("contains", other, value))
    yield _contains(value, other)


def _compatible(haystack, needle):
    if isinstance(haystack, str):
        return isinstance(needle, str)
    if isinstance(haystack, list):
        return isinstance(needle, list)
    if isinstance(haystack, dict):
        return isinstance(needle, dict)
    return False


def _b_inside(runtime, args, value):
    other = list(runtime.eval(args[0], value))[0]
    yield _contains(other, value)


def _contains(haystack, needle):
    if isinstance(haystack, str) and isinstance(needle, str):
        return needle in haystack
    if isinstance(haystack, list) and isinstance(needle, list):
        return all(any(_contains(h, n) for h in haystack) for n in needle)
    if isinstance(haystack, dict) and isinstance(needle, dict):
        return all(key in haystack and _contains(haystack[key], item)
                   for key, item in needle.items())
    return haystack == needle


def _b_index(runtime, args, value):
    needle = list(runtime.eval(args[0], value))[0]
    if isinstance(value, str) and isinstance(needle, str):
        found = value.find(needle)
        yield found if found >= 0 else None
    elif isinstance(value, list):
        for position, item in enumerate(value):
            if item == needle:
                yield position
                return
        yield None
    else:
        yield None


def _b_ascii_downcase(runtime, args, value):
    if not isinstance(value, str):
        raise JqError(_not_applied("ascii_downcase", value))
    yield "".join(c.lower() if "A" <= c <= "Z" else c for c in value)


def _b_ascii_upcase(runtime, args, value):
    if not isinstance(value, str):
        raise JqError(_not_applied("ascii_upcase", value))
    yield "".join(c.upper() if "a" <= c <= "z" else c for c in value)


def _b_floor(runtime, args, value):
    _require_number("floor", value)
    yield float(math.floor(value)) if isinstance(value, float) else math.floor(value)


def _b_ceil(runtime, args, value):
    _require_number("ceil", value)
    yield float(math.ceil(value)) if isinstance(value, float) else math.ceil(value)


def _b_round(runtime, args, value):
    _require_number("round", value)
    return_value = math.floor(value + 0.5) if isinstance(value, float) else value
    yield float(return_value) if isinstance(value, float) else return_value


def _b_fabs(runtime, args, value):
    _require_number("fabs", value)
    yield abs(value)


def _b_sqrt(runtime, args, value):
    _require_number("sqrt", value)
    yield math.sqrt(value)


def _require_number(fn, value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise JqError(_not_applied(fn, value))


def _b_to_entries(runtime, args, value):
    if isinstance(value, dict):
        yield [{"key": key, "value": value[key]} for key in sorted(value)]
    elif isinstance(value, list):
        yield [{"key": position, "value": item} for position, item in enumerate(value)]
    else:
        raise JqError(_not_applied("keys", value))


def _b_from_entries(runtime, args, value):
    if not isinstance(value, list):
        raise JqError(_not_applied("from_entries", value))
    out = {}
    for item in value:
        key = item.get("key", item.get("Key", item.get("name")))
        out[_key_text(key)] = item.get("value", item.get("Value"))
    yield out


def _b_with_entries(runtime, args, value):
    if isinstance(value, dict):
        entries = [{"key": key, "value": value[key]} for key in value]
    elif isinstance(value, list):
        entries = [{"key": position, "value": item} for position, item in enumerate(value)]
    else:
        raise JqError(_not_applied("keys", value))
    out = {}
    for entry in entries:
        for mapped in runtime.eval(args[0], entry):
            if not isinstance(mapped, dict):
                raise JqError("with_entries: the filter must answer an entry object")
            out[_key_text(mapped.get("key"))] = mapped.get("value")
    yield out


def _compile_re(pattern, flags=""):
    translated = pattern.replace("(?<", "(?P<")
    options = 0
    if "i" in flags:
        options |= re.IGNORECASE
    if "m" in flags:
        options |= re.MULTILINE
    if "s" in flags:
        options |= re.DOTALL
    if "x" in flags:
        options |= re.VERBOSE
    try:
        return re.compile(translated, options)
    except re.error as exc:
        detail = str(exc).split(" at position")[0].strip()
        raise JqError('invalid regular expression %s: error parsing regexp: %s: `%s`'
                      % (json.dumps(pattern), detail, pattern))


def _b_test(runtime, args, value):
    pattern = list(runtime.eval(args[0], value))[0]
    flags = list(runtime.eval(args[1], value))[0] if len(args) > 1 else None
    if not isinstance(value, str):
        raise JqError(_arg_not_applied2("test", [pattern, flags], value))
    yield _compile_re(pattern, flags or "").search(value) is not None


def _arg_not_applied2(fn, argvalues, value):
    shown = "; ".join(to_json(a) for a in argvalues)
    return "%s(%s) cannot be applied to: %s (%s)" % (
        fn, shown, _type_of(value), _preview(value))


def _b_match(runtime, args, value):
    pattern = list(runtime.eval(args[0], value))[0]
    flags = list(runtime.eval(args[1], value))[0] if len(args) > 1 else ""
    regex = _compile_re(pattern, flags)
    if not isinstance(value, str):
        raise JqError(_arg_not_applied("match", pattern, value))
    match = regex.search(value)
    if not match:
        return
    yield {
        "offset": match.start(),
        "length": match.end() - match.start(),
        "string": match.group(0),
        "captures": [
            {"offset": group.start() if group else -1,
             "length": (group.end() - group.start()) if group else 0,
             "string": group.group(0) if group else None,
             "name": _group_name(match.re, index)}
            for index, group in enumerate(match.groups(), start=1)
        ],
    }


def _group_name(regex, index):
    for name, position in regex.groupindex.items():
        if position == index:
            return name
    return None


def _b_capture(runtime, args, value):
    pattern = list(runtime.eval(args[0], value))[0]
    flags = list(runtime.eval(args[1], value))[0] if len(args) > 1 else ""
    regex = _compile_re(pattern, flags)
    if not isinstance(value, str):
        raise JqError(_arg_not_applied("capture", pattern, value))
    match = regex.search(value)
    if not match:
        return
    out = {}
    for name, text in match.groupdict().items():
        out[name] = text
    yield out


def _b_sub(runtime, args, value):
    pattern = list(runtime.eval(args[0], value))[0]
    replacement = list(runtime.eval(args[1], value))[0]
    flags = list(runtime.eval(args[2], value))[0] if len(args) > 2 else ""
    if not isinstance(value, str):
        raise JqError(_arg_not_applied("sub", pattern, value))
    regex = _compile_re(pattern, flags)
    yield regex.sub(_replacement(replacement), value, count=1)


def _b_gsub(runtime, args, value):
    pattern = list(runtime.eval(args[0], value))[0]
    replacement = list(runtime.eval(args[1], value))[0]
    flags = list(runtime.eval(args[2], value))[0] if len(args) > 2 else ""
    if not isinstance(value, str):
        raise JqError(_arg_not_applied("gsub", pattern, value))
    regex = _compile_re(pattern, flags)
    yield regex.sub(_replacement(replacement), value)


def _replacement(text):
    """jq's replacement strings use \\1 and \\\\ escapes."""
    def expand(match):
        out = []
        i = 0
        while i < len(text):
            if text[i] == "\\" and i + 1 < len(text):
                nxt = text[i + 1]
                if nxt.isdigit():
                    group = match.group(int(nxt)) if int(nxt) <= len(match.groups()) else ""
                    out.append(group or "")
                else:
                    out.append(nxt)
                i += 2
                continue
            out.append(text[i])
            i += 1
        return "".join(out)
    return expand


def _b_split_re(runtime, args, value):
    pattern = list(runtime.eval(args[0], value))[0]
    flags = list(runtime.eval(args[1], value))[0] if len(args) > 1 else ""
    if not isinstance(value, str):
        raise JqError(_arg_not_applied("splits", pattern, value))
    yield [item for item in _compile_re(pattern, flags).split(value)]


def _b_indices(runtime, args, value):
    needle = list(runtime.eval(args[0], value))[0]
    if isinstance(value, str) and isinstance(needle, str):
        out = []
        start = 0
        while True:
            found = value.find(needle, start)
            if found < 0:
                break
            out.append(found)
            start = found + 1
        yield out
    elif isinstance(value, list):
        yield [position for position, item in enumerate(value) if item == needle]
    else:
        yield []


def _b_implode(runtime, args, value):
    if not isinstance(value, list) or not all(
            isinstance(code, int) and not isinstance(code, bool) for code in value):
        raise JqError(_not_applied("implode", value))
    try:
        yield "".join(chr(code) for code in value)
    except (ValueError, OverflowError):
        raise JqError(_not_applied("implode", value))


def _b_explode(runtime, args, value):
    if not isinstance(value, str):
        raise JqError(_not_applied("explode", value))
    yield [ord(ch) for ch in value]


def _b_todate(runtime, args, value):
    import datetime
    yield datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _b_getpath(runtime, args, value):
    for path in runtime.eval(args[0], value):
        current = value
        for step in path:
            current = _index_value(current, step)
        yield current


def _b_del(runtime, args, value):
    paths = list(runtime.eval(args[0], value))
    out = value
    for path in paths:
        out = _delete_path(out, path)
    yield out


def _delete_path(value, path):
    if not path:
        return None
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        out = dict(value)
        if len(path) == 1:
            out.pop(head, None)
        elif head in out:
            out[head] = _delete_path(out[head], rest)
        return out
    if isinstance(value, list) and isinstance(head, int):
        out = list(value)
        if len(path) == 1:
            if 0 <= head < len(out):
                del out[head]
        elif 0 <= head < len(out):
            out[head] = _delete_path(out[head], rest)
        return out
    return value


_BUILTINS = {
    ("length", 0): _b_length,
    ("utf8bytelength", 0): lambda r, a, v: iter([len(v.encode())]),
    ("keys", 0): _b_keys,
    ("keys_unsorted", 0): _b_keys_unsorted,
    ("values", 0): _b_values,
    ("has", 1): _b_has,
    ("map", 1): _b_map,
    ("map_values", 1): _b_map_values,
    ("select", 1): _b_select,
    ("type", 0): _b_type,
    ("tostring", 0): _b_tostring,
    ("tonumber", 0): _b_tonumber,
    ("tojson", 0): _b_tojson,
    ("fromjson", 0): _b_fromjson,
    ("sort", 0): _b_sort,
    ("sort_by", 1): _b_sort_by,
    ("group_by", 1): _b_group_by,
    ("unique", 0): _b_unique,
    ("unique_by", 1): _b_unique_by,
    ("min", 0): lambda r, a, v: _minmax(r, a, v, False),
    ("max", 0): lambda r, a, v: _minmax(r, a, v, True),
    ("min_by", 1): lambda r, a, v: _minmax_by(r, a, v, False),
    ("max_by", 1): lambda r, a, v: _minmax_by(r, a, v, True),
    ("add", 0): _b_add,
    ("any", 0): _b_any,
    ("any", 1): _b_any,
    ("all", 0): _b_all,
    ("all", 1): _b_all,
    ("flatten", 0): _b_flatten,
    ("flatten", 1): _b_flatten,
    ("reverse", 0): _b_reverse,
    ("range", 1): _b_range,
    ("range", 2): _b_range,
    ("empty", 0): _b_empty,
    ("error", 0): _b_error,
    ("error", 1): _b_error,
    ("not", 0): _b_not,
    ("first", 0): _b_first,
    ("first", 1): _b_first,
    ("last", 0): _b_last,
    ("last", 1): _b_last,
    ("nth", 1): _b_nth,
    ("nth", 2): _b_nth,
    ("limit", 2): _b_limit,
    ("join", 1): _b_join,
    ("split", 1): _b_split,
    ("splits", 1): _b_split_re,
    ("splits", 2): _b_split_re,
    ("startswith", 1): _b_startswith,
    ("endswith", 1): _b_endswith,
    ("ltrimstr", 1): _b_ltrimstr,
    ("rtrimstr", 1): _b_rtrimstr,
    ("contains", 1): _b_contains,
    ("inside", 1): _b_inside,
    ("index", 1): _b_index,
    ("indices", 1): _b_indices,
    ("ascii_downcase", 0): _b_ascii_downcase,
    ("ascii_upcase", 0): _b_ascii_upcase,
    ("floor", 0): _b_floor,
    ("ceil", 0): _b_ceil,
    ("round", 0): _b_round,
    ("fabs", 0): _b_fabs,
    ("sqrt", 0): _b_sqrt,
    ("to_entries", 0): _b_to_entries,
    ("from_entries", 0): _b_from_entries,
    ("with_entries", 1): _b_with_entries,
    ("test", 1): _b_test,
    ("test", 2): _b_test,
    ("match", 1): _b_match,
    ("match", 2): _b_match,
    ("capture", 1): _b_capture,
    ("capture", 2): _b_capture,
    ("sub", 2): _b_sub,
    ("sub", 3): _b_sub,
    ("gsub", 2): _b_gsub,
    ("gsub", 3): _b_gsub,
    ("implode", 0): _b_implode,
    ("explode", 0): _b_explode,
    ("todate", 0): _b_todate,
    ("getpath", 1): _b_getpath,
    ("del", 1): _b_del,
}


# ---------------------------------------------------------------- entry

def _emit(value):
    """One result as gh prints it: null is a blank line, strings raw, the
    rest compact JSON (top-level floats at two decimals unless integral)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number_out(value)
    return to_json(value)


def _format_parse_error(expr, exc):
    """gojq's block:

        failed to parse jq expression (line 1, column 4)
            .a[
               ^  unexpected EOF
    """
    pos = exc.pos if exc.pos is not None else len(expr)
    head = "failed to parse jq expression (line 1, column %d)" % (pos + 1)
    return "%s\n    %s\n%s^  %s" % (head, expr, " " * (4 + pos), exc.text)


def run(expr, data, raw_output=False):
    """Apply `expr` to `data` and return the text `gh --jq` would print."""
    if not isinstance(expr, str):
        raise JqError("expression must be a string")
    try:
        ast = _Parser(_tokenize(expr)).parse()
    except _ParseError as exc:
        raise JqError(_format_parse_error(expr, exc))
    runtime = _Runtime()
    out = []
    try:
        for value in runtime.eval(ast, data):
            out.append(_emit(value))
    except RecursionError:
        raise JqError("expression recursed too deeply")
    return "".join(item + "\n" for item in out)
