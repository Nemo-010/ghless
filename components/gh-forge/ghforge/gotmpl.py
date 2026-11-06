"""A Go text/template subset, for `gh --template` / `-t`.

Everything here was measured against the real gh (which embeds Go's
text/template plus gh's own FuncMap), not assumed:

    gh api repos/pkgforge/soar -t '{{.name}}'      -> soar        (no newline)
    gh api repos/pkgforge/soar -t '{{.nope}}'      -> <no value>  (missing keys!)
    gh api repos/pkgforge/soar -t '{{nope}}'       -> template: :1: function "nope" not defined, exit 1
    gh api repos/pkgforge/soar -t '{{.license}}'   -> map[key:mit ...]   (Go map syntax, sorted)
    gh api repos/pkgforge/soar -t '{{printf "%d" .stargazers_count}}'
                                                   -> %!d(float64=895)  (JSON numbers are float64)
    gh api repos/pkgforge/soar -t ''               -> the raw JSON (empty template means none)

gh's FuncMap, as measured:
    join      jq's join (nulls become ""; wrong types raise "cannot add")
    pluck     values of a key across an array of objects (missing -> nil)
    tablerow  append cells to a pending row (output fully deferred)
    tablerender  flush pending rows, each cell padded to width+2, row + "\\n"
    timeago   just now / N minutes ago / N hours ago / N months ago / N years ago
    truncate  len <= n ? text : first n-3 + "..."  (n < 4 -> first n)
    color     ANSI only when colour is on (CLICOLOR_FORCE or a TTY)
    autocolor same, but an empty spec renders plain even with colour on
    hyperlink OSC-8 hyperlink, always emitted
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys


class TemplateError(Exception):
    """A parse or execution failure, phrased the way text/template phrases it."""


# ---------------------------------------------------------------- tokens

_ACTION_RE = re.compile(r"\{\{/\*.*?\*/\}\}|\{\{-?|-?\}\}", re.S)
_SPACE_RE = re.compile(r"\s+")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _tokenize(template):
    """Split into (text, action-inner) pieces.

    Pieces: ("text", body, None) and ("open", inner, offset) where offset is the
    0-based byte offset of the "{{" in the template. {{- and -}} trim the
    adjoining whitespace, matching text/template.
    """
    pieces = []
    pos = 0
    length = len(template)
    while pos < length:
        brace = template.find("{{", pos)
        if brace < 0:
            rest = template[pos:]
            if "{{" in rest:
                raise TemplateError("unclosed action")
            pieces.append(("text", rest, None))
            break
        if brace > pos:
            pieces.append(("text", template[pos:brace], None))
        close = template.find("}}", brace + 2)
        if close < 0:
            raise TemplateError("unclosed action")
        inner = template[brace + 2:close]
        open_kind = "open"
        if inner.startswith("-"):
            inner = inner[1:]
            open_kind = "open_trim"
            if pieces and pieces[-1][0] == "text":
                pieces[-1] = ("text", pieces[-1][1].rstrip(), None)
        close_kind = "close"
        if inner.endswith("-"):
            inner = inner[:-1]
            close_kind = "close_trim"
        offset = brace
        pieces.append((open_kind, inner, offset))
        pieces.append((close_kind, "}}", close))
        pos = close + 2
        if close_kind == "close_trim":
            while pos < length and template[pos] in " \t\r\n":
                pos += 1
    # comments: strip actions whose body starts with "/*" ... simplest: drop them here
    cleaned = []
    index = 0
    while index < len(pieces):
        kind, value, where = pieces[index]
        if kind == "open" and value.lstrip().startswith("/*"):
            index += 2  # skip the action entirely
            continue
        cleaned.append((kind, value, where))
        index += 1
    return cleaned


# ---------------------------------------------------------------- lexer for actions

def _lex_action(source):
    """An action body -> token list with (kind, value, offset)."""
    tokens = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == '"':
            j = i + 1
            out = []
            while j < n:
                if source[j] == "\\":
                    if j + 1 >= n:
                        raise TemplateError("unterminated quoted string")
                    esc = source[j + 1]
                    out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"',
                                "\\": "\\", "'": "'"}.get(esc, esc))
                    j += 2
                    continue
                if source[j] == '"':
                    break
                out.append(source[j])
                j += 1
            if j >= n:
                raise TemplateError("unterminated quoted string")
            tokens.append(("string", "".join(out), i))
            i = j + 1
            continue
        if ch == "`":
            j = source.find("`", i + 1)
            if j < 0:
                raise TemplateError("unterminated raw string")
            tokens.append(("string", source[i + 1:j], i))
            i = j + 1
            continue
        if ch == "$":
            match = _NAME_RE.match(source, i + 1)
            if match:
                tokens.append(("variable", match.group(0), i))
                i = match.end()
            else:
                tokens.append(("variable", "$", i))
                i += 1
            continue
        if ch == ".":
            j = i
            parts = []
            while j < n and source[j] == ".":
                match = _NAME_RE.match(source, j + 1)
                if not match:
                    break
                parts.append(match.group(0))
                j = match.end()
            if parts:
                tokens.append(("field", parts, i))
                i = j
            else:
                tokens.append(("dot", ".", i))
                i += 1
            continue
        if ch.isdigit():
            match = _NUMBER_RE.match(source, i)
            raw = match.group(0)
            value = float(raw) if any(c in raw for c in ".eE") else int(raw)
            tokens.append(("number", value, i))
            i = match.end()
            continue
        if ch == ",":
            tokens.append(("comma", ",", i))
            i += 1
            continue
        if ch == "|":
            tokens.append(("pipe", "|", i))
            i += 1
            continue
        if ch == "(":
            tokens.append(("lparen", "(", i))
            i += 1
            continue
        if ch == ")":
            tokens.append(("rparen", ")", i))
            i += 1
            continue
        if source.startswith(":=", i):
            tokens.append(("assign", ":=", i))
            i += 2
            continue
        match = _NAME_RE.match(source, i)
        if match:
            tokens.append(("name", match.group(0), i))
            i = match.end()
            continue
        raise TemplateError("unexpected %r in action" % ch)
    return tokens


# ---------------------------------------------------------------- parser

class _Node:
    pass


class _Text(_Node):
    def __init__(self, text):
        self.text = text


class _Action(_Node):
    def __init__(self, pipeline):
        self.pipeline = pipeline


class _If(_Node):
    def __init__(self, branches, else_body):
        self.branches = branches      # [(condition, body)]
        self.else_body = else_body


class _With(_Node):
    def __init__(self, condition, body, else_body):
        self.condition = condition
        self.body = body
        self.else_body = else_body


class _Range(_Node):
    def __init__(self, pipeline, body, else_body, key_var, value_var):
        self.pipeline = pipeline
        self.body = body
        self.else_body = else_body
        self.key_var = key_var
        self.value_var = value_var


class _Assign(_Node):
    def __init__(self, name, pipeline):
        self.name = name
        self.pipeline = pipeline


class _Break(_Node):
    pass


class _Continue(_Node):
    pass


class _Parser:
    KEYWORDS = {"if", "range", "with", "else", "break", "continue", "end"}

    def __init__(self, template):
        self.template = template
        self.pieces = _tokenize(template)
        self.index = 0

    def parse(self):
        body = self.parse_list(("__done__", None))
        if self.index < len(self.pieces):
            kind, value, _ = self.pieces[self.index]
            if kind == "open":
                raise TemplateError("unexpected {{end}}")
            raise TemplateError("unexpected %r" % value)
        return body

    def peek(self):
        if self.index < len(self.pieces):
            return self.pieces[self.index]
        return ("eof", None, None)

    def next(self):
        piece = self.pieces[self.index]
        self.index += 1
        return piece

    def parse_list(self, stop):
        """Parse until a stop action ('end', 'else', or both)."""
        nodes = []
        while self.index < len(self.pieces):
            kind, value, _ = self.peek()
            if kind in ("open", "open_trim"):
                _, _, action_where = self.peek()
                stripped = self._peek_source().strip()
                keyword = stripped.split(None, 1)[0] if stripped else ""
                if keyword in self.KEYWORDS or keyword == "end":
                    if stop[0] == "__block__" and keyword in ("end", "else"):
                        return nodes
                    if keyword == stop[0] and (stop[1] is None or keyword == stop[1]):
                        return nodes
                    if keyword == "end":
                        raise TemplateError("unexpected {{end}}")
                    self._action_source()   # consume the action we just peeked
                    nodes.append(self.parse_action(keyword, stripped, action_where))
                    continue
                self._action_source()       # consume before parsing its value
                tokens = _lex_action(stripped)
                if tokens and tokens[0][0] == "variable" and \
                        len(tokens) > 1 and tokens[1][0] == "assign":
                    pipeline = self.parse_pipeline(
                        stripped[stripped.index(":=") + 2:].strip(), action_where)
                    nodes.append(_Assign(tokens[0][1], pipeline))
                else:
                    nodes.append(_Action(self.parse_pipeline(stripped, action_where + 2)))
                continue
            if kind == "text":
                self.next()
                nodes.append(_Text(value))
                continue
            self.next()
        if stop[0] in ("end", "else"):
            raise TemplateError("unclosed action")
        return nodes

    def _peek_source(self):
        """The inner text of the next action, without consuming it."""
        if self.index < len(self.pieces) and \
                self.pieces[self.index][0] in ("open", "open_trim"):
            return self.pieces[self.index][1] or ""
        return ""

    def _action_source(self):
        """Consume one {{ ... }} piece and return its inner source."""
        piece = self.next()  # ('open', inner, where)
        inner = piece[1] or ""
        if self.index < len(self.pieces) and \
                self.pieces[self.index][0] in ("close", "close_trim"):
            self.index += 1
        else:
            raise TemplateError("unclosed action")
        self._last_else_inner = inner
        return inner

    def parse_action(self, keyword, source, where=0):
        rest = source[len(keyword):].strip()
        # gh's column is the 0-based byte offset in the template: '{{', the keyword,
        # and one space, plus the offset within the rest.
        self._rest_base = where + 2 + len(keyword) + 1
        if keyword == "if":
            return self._parse_if(rest)
        if keyword == "with":
            return self._parse_with(rest)
        if keyword == "range":
            return self._parse_range(rest)
        if keyword == "break":
            return _Break()
        if keyword == "continue":
            return _Continue()
        tokens = _lex_action(rest)
        if tokens and tokens[0][0] == "variable" and \
                len(tokens) > 1 and tokens[1][0] == "assign":
            pipeline = self.parse_pipeline(rest[rest.index(":=") + 2:].strip())
            return _Assign(tokens[0][1], pipeline)
        return _Action(self.parse_pipeline(rest))

    def _split_else(self, source):
        """Split 'cond' from a trailing `{{else}}` inside this action's text:
        gh templates put else in its own action, so nothing to do here."""
        return source

    def _collect_block(self, keyword):
        body = self.parse_list(("end", None))
        if self.index >= len(self.pieces):
            raise TemplateError("unclosed action")
        self.next()  # the {{end}}
        return body

    def _peek_is_else(self):
        if self.index >= len(self.pieces):
            return False
        if self.peek()[0] != "open":
            return False
        return self._peek_source().strip().startswith("else")

    def _parse_if(self, condition):
        branches = []
        node = None
        cond = condition
        while True:
            body = self.parse_list(("__block__", None))
            branches.append((self.parse_pipeline(cond, getattr(self, "_rest_base", 0)), body))
            if self._peek_is_else():
                self._action_source()   # consume the {{else ...}} action
                inner = (self._last_else_inner or "").strip()
                if inner.startswith("else if"):
                    cond = inner[len("else if"):].strip()
                    continue
                else_body = self.parse_list(("end", None))
                self._action_source()   # consume {{end}}
                return _If(branches, else_body)
            self._action_source()   # consume {{end}}
            return _If(branches, None)

    def _parse_with(self, condition):
        body = self.parse_list(("__block__", None))
        else_body = None
        if self._peek_is_else():
            self._action_source()
            else_body = self.parse_list(("end", None))
        self._action_source()   # {{end}}
        return _With(self.parse_pipeline(condition, getattr(self, "_rest_base", 0)),
                     body, else_body)

    def _parse_range(self, source):
        key_var = value_var = None
        pipeline_text = source
        assign = " := " in (" " + source + " ")
        tokens = [t for t in _lex_action(source) if t[0] != "comma"]
        if tokens and tokens[0][0] == "variable":
            if len(tokens) > 2 and tokens[1][0] == "variable" and tokens[2][0] == "assign":
                key_var = tokens[0][1]
                value_var = tokens[1][1]
                pipeline_text = source[source.index(":=") + 2:].strip()
            elif len(tokens) > 1 and tokens[1][0] == "assign":
                value_var = tokens[0][1]
                pipeline_text = source[source.index(":=") + 2:].strip()
        body = self.parse_list(("__block__", None))
        else_body = None
        if self._peek_is_else():
            self._action_source()
            else_body = self.parse_list(("end", None))
        self._action_source()   # {{end}}
        return _Range(self.parse_pipeline(pipeline_text, getattr(self, "_rest_base", 0)),
                      body, else_body, key_var, value_var)

    # -- pipelines -------------------------------------------------------
    def parse_pipeline(self, text, base=0):
        tokens = _lex_action(text)
        parser = _PipelineParser(tokens, text, base)
        return parser.parse()


class _PipelineParser:
    """cmd (| cmd)* where a cmd is operand or func arg1 arg2 ..."""

    def __init__(self, tokens, source, base=0):
        self.tokens = tokens
        self.source = source
        self.base = base
        self.index = 0

    def peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index][:2]
        return ("eof", None)

    def next(self):
        token = self.tokens[self.index]
        self.index += 1
        return token

    def parse(self):
        stages = [self.parse_command()]
        while self.peek()[0] == "pipe":
            self.next()
            stages.append(self.parse_command())
        if self.peek()[0] != "eof":
            raise TemplateError("unexpected %r in %s" % (self.peek()[1], self.source))
        if len(stages) == 1:
            return stages[0]
        return ("pipeline", stages)

    def parse_command(self):
        start = self._pos()
        first = self.parse_operand()
        args = []
        arg_offsets = []
        while self.peek()[0] not in ("eof", "pipe", "rparen"):
            before = self._pos()
            args.append(self.parse_operand())
            arg_offsets.append(before)
        end = self._pos()
        if not args:
            if first[0] == "func":
                return ("call", first[1], [], self.source[start:end].strip(),
                        self.base + start)
            return first
        if first[0] != "func":
            raise TemplateError("unexpected %r in %s" % (args[0], self.source))
        return ("call", first[1], args, self.source[start:end].strip(),
                [self.base + offset for offset in arg_offsets],
                self.base + start)

    def _pos(self):
        """Character offset of the upcoming token in the action source."""
        if self.index >= len(self.tokens):
            return len(self.source)
        return self.tokens[self.index][2]

    def parse_operand(self):
        kind, value = self.peek()
        if kind == "lparen":
            self.next()
            inner = self.parse()
            if self.peek()[0] != "rparen":
                raise TemplateError("unclosed parenthesis")
            self.next()
            return inner
        if kind == "string":
            self.next()
            return ("const", value)
        if kind == "number":
            self.next()
            return ("const", value)
        if kind == "variable":
            self.next()
            return ("variable", value)
        if kind == "field":
            self.next()
            return ("field", value)
        if kind == "dot":
            self.next()
            return ("identity",)
        if kind == "name":
            self.next()
            if value in ("true", "false"):
                return ("const", value == "true")
            if value == "nil":
                return ("const", None)
            if value in _FUNCS:
                return ("func", value)
            raise TemplateError('function "%s" not defined' % value)
        raise TemplateError("unexpected %r" % (value,))


# ---------------------------------------------------------------- values

def _go_str(value):
    """How text/template prints a value (fmt %v)."""
    if value is None:
        return "<nil>"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + " ".join(_go_str(item) for item in value) + "]"
    if isinstance(value, dict):
        return "map[" + " ".join(
            "%s:%s" % (key, _go_str(value[key])) for key in sorted(value)) + "]"
    return str(value)


def _go_print(value):
    """`{{.x}}` prints via fmt.Sprint: nil is <no value> at top level."""
    if value is None:
        return "<no value>"
    return _go_str(value)


def _truthy(value):
    if value is None or value is False:
        return False
    return True


def _lookup(data, path):
    current = data
    for part in path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            current = None
    return current


class _Writer:
    """tablerow/tablerender share one deferred buffer per render call.

    tablerow only queues cells; a new tablerow commits the previous row to the
    pending buffer; tablerender commits the pending row and flushes the buffer
    into the output stream at that exact point (gh's measured behaviour: all
    literals before the render print first, then the rows).
    """

    def __init__(self, out):
        self.out = out
        self.pending = []
        self.pending_cells = []

    def row(self, cells):
        if self.pending_cells:
            self.pending.append(self._render_row(self.pending_cells))
        self.pending_cells = list(cells)

    def render_row(self):
        if self.pending_cells:
            self.pending.append(self._render_row(self.pending_cells))
            self.pending_cells = []
        if self.pending:
            self.out.append("".join(self.pending))
            self.pending = []

    def flush_all(self):
        """gh flushes pending rows at the end of the template."""
        self.render_row()

    @staticmethod
    def _render_row(cells):
        pieces = []
        for index, cell in enumerate(cells):
            text = _go_str(cell).split("\n", 1)[0]
            pieces.append(text)
            if index < len(cells) - 1:
                pieces.append("  ")
        return "".join(pieces) + "\n"


class _State:
    def __init__(self, data, colour, out=None):
        self.data = data
        self.variables = {"$": data}
        self.writer = _Writer(out if out is not None else [])
        self.colour = colour

    def set(self, name, value):
        self.variables[name] = value

    def get(self, name):
        if name in self.variables:
            return self.variables[name]
        raise TemplateError('undefined variable "$%s"' % name)


def _eval(node, state):
    kind = node[0]
    if kind == "identity":
        yield state.data
    elif kind == "const":
        yield node[1]
    elif kind == "field":
        yield _lookup(state.data, node[1])
    elif kind == "variable":
        name = node[1]
        yield state.data if name == "$" else state.get(name)
    elif kind == "pipeline":
        values = list(_eval(node[1][0], state))
        for stage in node[1][1:]:
            fed = []
            for item in values:
                sub = _state_with(state, item)
                if stage[0] == "call":
                    fed.extend(_eval(("call", stage[1], stage[2] + [("identity",)],
                                      stage[3], stage[4], stage[5]), sub))
                else:
                    fed.extend(_eval(stage, sub))
            values = fed
        for item in values:
            yield item
    elif kind == "call":
        func = _FUNCS[node[1]]
        name = node[1]
        shown = node[3] if len(node) > 3 else node[1]
        arg_cols = node[4] if len(node) > 4 else []
        signature = _SIGNATURES.get(name)
        arg_lists = []
        for position, arg in enumerate(node[2]):
            try:
                values = list(_eval(arg, state))
            except TemplateError as exc:
                message = str(exc)
                if message.startswith("template:"):
                    raise
                column = arg_cols[position] if position < len(arg_cols) else 2
                raise TemplateError('template: :1:%d: executing "" at <%s>: %s'
                                    % (column, _arg_shown(arg), message))
            if signature and position < len(signature) and signature[position]:
                for value in values:
                    problem = _signature_mismatch(signature[position], value)
                    if problem:
                        column = arg_cols[position] if position < len(arg_cols) else 2
                        raise TemplateError(
                            'template: :1:%d: executing "" at <%s>: wrong type for '
                            'value; expected %s; got %s'
                            % (column, _arg_shown(arg), signature[position],
                               _type_name(value)))
            arg_lists.append(values)
        if not arg_lists:
            combos = [[]]
        elif len(arg_lists) == 1:
            combos = [[item] for item in arg_lists[0]]
        else:
            from itertools import product
            combos = list(product(*arg_lists))
        column = node[5] if len(node) > 5 else 2
        for combo in combos:
            try:
                yield from func(state, list(combo))
            except TemplateError as exc:
                message = str(exc)
                if message.startswith("template:"):
                    raise
                raise TemplateError(
                    'template: :1:%d: executing "" at <%s>: error calling %s: %s'
                    % (column, shown, name, message))
    else:
        raise TemplateError("unsupported node %r" % kind)


def _dispatch_eval(node, state):
    return _eval(node, state)


def _signature_mismatch(expected, value):
    if expected == "string":
        return not isinstance(value, str)
    if expected == "int":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) \
            and float(value).is_integer()
        return not ok
    if expected == "[]interface {}":
        return not isinstance(value, list)
    return False


def _arg_shown(arg):
    """gh shows the argument's source text (e.g. `.description`)."""
    if arg[0] == "field":
        return "." + ".".join(arg[1])
    if arg[0] == "const" and isinstance(arg[1], str):
        return json.dumps(arg[1])
    if arg[0] == "const":
        return _go_str(arg[1])
    if arg[0] == "call":
        return arg[3] if len(arg) > 3 else arg[1]
    if arg[0] == "identity":
        return "."
    if arg[0] == "variable":
        return "$" + arg[1]
    return "?"


# ---------------------------------------------------------------- funcs

def _f_join(state, args):
    if len(args) != 2:
        raise TemplateError("wrong number of args for join: want 2 got %d" % len(args))
    separator, array = args
    if not isinstance(separator, str):
        raise TemplateError("wrong type for value; expected string; got %s"
                            % _type_name(separator))
    if not isinstance(array, list):
        raise TemplateError("wrong type for value; expected string; got %s"
                            % _type_name(array))
    from .jqish import _arith, to_text  # reuse jq's join semantics
    out = ""
    for item in array:
        piece = "" if item is None else to_text(item)
        if not out:
            out = piece
        else:
            out = _arith("+", out, separator)
            out = _arith("+", out, piece)
    yield out


def _f_pluck(state, args):
    key, array = args
    if array is None:
        yield []
        return
    if not isinstance(array, list):
        raise TemplateError("wrong type for value; expected []interface {}; got %s"
                            % _type_name(array))
    yield [item.get(key) if isinstance(item, dict) else None for item in array]


def _type_name(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "float64" if isinstance(value, float) else "int"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "[]interface {}"
    if isinstance(value, dict):
        return "map[string]interface {}"
    return type(value).__name__


_ANSI = {
    "black": 30, "red": 31, "green": 32, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "gray": 30, "grey": 30, "default": 39,
}


def _colour_on():
    if os.environ.get("CLICOLOR_FORCE") and os.environ["CLICOLOR_FORCE"] != "0":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _f_color(state, args):
    spec, text = args[0], args[1]
    if not isinstance(spec, str) or spec not in _ANSI:
        spec = "gray" if spec else ""
    if not state.colour or not spec:
        yield text
        return
    yield "\x1b[0;%dm%s\x1b[0m" % (_ANSI[spec], text)


def _f_autocolor(state, args):
    spec, text = args[0], args[1]
    if not state.colour or not spec or spec not in _ANSI:
        yield text
        return
    yield "\x1b[0;%dm%s\x1b[0m" % (_ANSI[spec], text)


def _f_hyperlink(state, args):
    url, text = args[0], args[1]
    yield "\x1b]8;;%s\x1b\\%s\x1b]8;;\x1b\\" % (url, text)


_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$")


def _f_timeago(state, args):
    text = args[0]
    if not isinstance(text, str):
        raise TemplateError("expected string; found %s" % _type_name(text))
    if not _TIME_RE.match(text):
        raise TemplateError('parsing time "%s" as "2006-01-02T15:04:05Z07:00": '
                            'cannot parse "%s" as "2006"' % (text, text))
    cleaned = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        when = datetime.datetime.fromisoformat(cleaned)
    except ValueError:
        raise TemplateError('parsing time "%s" as "2006-01-02T15:04:05Z07:00": '
                            'cannot parse "%s" as "2006"' % (text, text))
    now = datetime.datetime.now(datetime.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    seconds = abs(int((now - when).total_seconds()))
    if seconds < 60:
        yield "just now"
        return
    if seconds < 3600:
        yield "%d minutes ago" % (seconds // 60)
        return
    if seconds < 86400:
        yield "%d hours ago" % (seconds // 3600)
        return
    days = seconds // 86400
    if days < 30:
        yield "1 day ago" if days == 1 else "%d days ago" % days
        return
    if days < 365:
        yield "%d months ago" % (days // 30)
        return
    yield "%d years ago" % (days // 365)


def _f_truncate(state, args):
    size, text = args
    if isinstance(size, float) and size.is_integer():
        size = int(size)
    if not isinstance(size, (int, float)) or isinstance(size, bool):
        raise TemplateError("wrong type for value; expected int; got %s"
                            % _type_name(size))
    if not isinstance(text, str):
        raise TemplateError("wrong type for value; expected string; got %s"
                            % _type_name(text))
    if len(text) <= size:
        yield text
        return
    if size < 4:
        yield text[:size]
        return
    yield text[:size - 3] + "..."


def _f_tablerow(state, args):
    state.writer.row(args)
    yield ""


def _f_tablerender(state, args):
    state.writer.render_row()
    yield ""


def _f_printf(state, args):
    fmt = args[0]
    if not isinstance(fmt, str):
        raise TemplateError("wrong type for value; expected string; got %s"
                            % _type_name(fmt))
    yield _sprintf(fmt, args[1:])


_SPRINTF = re.compile("%[-+ #0]*[0-9]*(?:\\.[0-9]+)?[a-zA-Z%]")


def _sprintf(fmt, args):
    out = []
    index = 0
    arg_index = 0
    while index < len(fmt):
        if fmt[index] != "%":
            out.append(fmt[index])
            index += 1
            continue
        match = _SPRINTF.match(fmt, index)
        if not match:
            out.append("%!(NOVERB)")
            break
        verb = match.group(0)
        index = match.end()
        if verb == "%%":
            out.append("%")
            continue
        flags = verb[1:-1]
        conversion = verb[-1]
        if arg_index >= len(args):
            out.append("%!" + verb[1:] + "(MISSING)")
            continue
        arg = args[arg_index]
        arg_index += 1
        if conversion == "d":
            if isinstance(arg, float) and not isinstance(arg, bool):
                out.append("%%!d(float64=%s)" % _float_text(arg))
            elif isinstance(arg, int) and not isinstance(arg, bool):
                out.append(str(arg))
            else:
                out.append("%%!d(%s=%s)" % (_type_name(arg), _go_str(arg)))
        elif conversion == "v":
            out.append(_go_str(arg))
        elif conversion == "s":
            out.append(_go_str(arg))
        elif conversion == "q":
            out.append(json.dumps(str(arg)))
        elif conversion == "t":
            out.append("true" if _truthy(arg) else "false")
        elif conversion in "fxXgG":
            number = _as_number(arg)
            width, precision = _width_precision(flags)
            text = "%*.*f" % (width or 0, precision if precision else 6, number)
            out.append(text)
        elif conversion == "T":
            out.append(_type_name(arg))
        else:
            out.append("%!" + verb + "(" + _type_name(arg) + "=" + _go_str(arg) + ")")
    if arg_index < len(args):
        out.append("%!(EXTRA " + ", ".join(
            "%s=%s" % (_type_name(item), _go_str(item)) for item in args[arg_index:]) + ")")
    return "".join(out)


def _float_text(value):
    return str(int(value)) if float(value).is_integer() else repr(value)


def _as_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    raise TemplateError("expected number, got %s" % _type_name(value))


def _width_precision(flags):
    if "." in flags:
        head, _, precision = flags.partition(".")
        return int(head or 0), int(precision or 0)
    return int(flags or 0), None


def _compare(op, left, right):
    left_num = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_num = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_num and right_num:
        # Go: an int literal and a float64 value are incompatible for lt/gt/...
        if isinstance(left, float) != isinstance(right, float):
            raise TemplateError("incompatible types for comparison: %s and %s"
                                % (_type_name(left), _type_name(right)))
    elif type(left) is not type(right) and not (left is None or right is None):
        raise TemplateError("incompatible types for comparison: %s and %s"
                            % (_type_name(left), _type_name(right)))
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    try:
        if op == "lt":
            return left < right
        if op == "le":
            return left <= right
        if op == "gt":
            return left > right
        if op == "ge":
            return left >= right
    except TypeError:
        raise TemplateError("incompatible types for comparison: %s and %s"
                            % (_type_name(left), _type_name(right)))
    raise TemplateError("unsupported comparison %r" % op)


def _f_eq(state, args):
    yield all(_compare("eq", args[0], arg) for arg in args[1:])


def _f_ne(state, args):
    yield not _compare("eq", args[0], args[1])


def _f_lt(state, args):
    yield _compare("lt", args[0], args[1])


def _f_le(state, args):
    yield _compare("le", args[0], args[1])


def _f_gt(state, args):
    yield _compare("gt", args[0], args[1])


def _f_ge(state, args):
    yield _compare("ge", args[0], args[1])


def _f_and(state, args):
    for arg in args:
        if not _truthy(arg):
            yield arg
            return
    yield args[-1]


def _f_or(state, args):
    for arg in args:
        if _truthy(arg):
            yield arg
            return
    yield args[-1]


def _f_not(state, args):
    yield not _truthy(args[0])


def _f_len(state, args):
    value = args[0]
    if value is None:
        raise TemplateError("len of nil pointer")
    if isinstance(value, (str, list, dict)):
        yield len(value)
        return
    raise TemplateError("len of type %s" % _type_name(value))


def _f_index(state, args):
    collection = args[0]
    for key in args[1:]:
        if isinstance(collection, dict):
            collection = collection.get(key)
        elif isinstance(collection, list):
            if not isinstance(key, (int, float)) or isinstance(key, bool):
                raise TemplateError("cannot index slice with %s" % _type_name(key))
            index = int(key)
            if index < 0 or index >= len(collection):
                raise TemplateError("index out of range: %d" % index)
            collection = collection[index]
        else:
            raise TemplateError("can't index item of type %s" % _type_name(collection))
    yield collection


def _f_print(state, args):
    yield _go_str_list(args)


def _go_str_list(args):
    return "".join(_go_str(item) for item in args)


def _f_println(state, args):
    yield _go_str_list(args) + "\n"


def _f_sprint(state, args):
    yield _go_str_list(args)


def _f_html(state, args):
    text = args[0] if args else ""
    yield (str(text).replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;").replace('"', "&#34;"))


def _f_js(state, args):
    yield json.dumps(args[0] if args else "")


_SIGNATURES = {
    "join": ("string", "[]interface {}"),
    "truncate": ("int", "string"),
    "timeago": ("string",),
    "pluck": ("string", None),
}

_FUNCS = {
    "join": _f_join,
    "pluck": _f_pluck,
    "tablerow": _f_tablerow,
    "tablerender": _f_tablerender,
    "timeago": _f_timeago,
    "truncate": _f_truncate,
    "color": _f_color,
    "autocolor": _f_autocolor,
    "hyperlink": _f_hyperlink,
    "printf": _f_printf,
    "eq": _f_eq,
    "ne": _f_ne,
    "lt": _f_lt,
    "le": _f_le,
    "gt": _f_gt,
    "ge": _f_ge,
    "and": _f_and,
    "or": _f_or,
    "not": _f_not,
    "len": _f_len,
    "index": _f_index,
    "print": _f_print,
    "println": _f_println,
    "sprint": _f_sprint,
    "html": _f_html,
    "js": _f_js,
}


# ---------------------------------------------------------------- executor

class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


def _execute(nodes, state, out):
    for node in nodes:
        if isinstance(node, _Text):
            out.append(node.text)
        elif isinstance(node, _Action):
            for value in _dispatch_eval(node.pipeline, state):
                out.append(_go_print(value))
        elif isinstance(node, _Assign):
            for value in _dispatch_eval(node.pipeline, state):
                state.set(node.name, value)
                break
        elif isinstance(node, _If):
            _execute_if(node, state, out)
        elif isinstance(node, _With):
            for value in _dispatch_eval(node.condition, state):
                if _truthy(value):
                    sub = _state_with(state, value)
                    _execute(node.body, sub, out)
                elif node.else_body is not None:
                    _execute(node.else_body, state, out)
                return
            if not True:
                pass
        elif isinstance(node, _Range):
            _execute_range(node, state, out)
        elif isinstance(node, _Break):
            raise _BreakSignal()
        elif isinstance(node, _Continue):
            raise _ContinueSignal()


def _state_with(state, data):
    sub = _State(state.data, state.colour)
    sub.variables = dict(state.variables)
    sub.data = data
    sub.writer = state.writer
    return sub


def _execute_if(node, state, out):
    for condition, body in node.branches:
        matched = False
        for value in _dispatch_eval(condition, state):
            matched = True
            if _truthy(value):
                _execute(body, state, out)
                return
        if matched:
            continue
    if node.else_body is not None:
        _execute(node.else_body, state, out)


def _execute_range(node, state, out):
    items = list(_dispatch_eval(node.pipeline, state))
    if items == [None]:
        items = []
    if not items:
        if node.else_body is not None:
            _execute(node.else_body, state, out)
        return
    if isinstance(items[0], dict):
        pairs = [(key, items[0][key]) for key in sorted(items[0])]
    elif isinstance(items[0], list):
        pairs = list(enumerate(items[0]))
    else:
        pairs = [(0, items[0])]
    try:
        for key, item in pairs:
            sub = _state_with(state, item)
            if node.value_var:
                if node.key_var:
                    sub.set(node.key_var, key)
                sub.set(node.value_var, item)
            try:
                _execute(node.body, sub, out)
            except _ContinueSignal:
                continue
            except _BreakSignal:
                break
    except IndexError:
        pass


# ---------------------------------------------------------------- entry

def _to_go_numbers(value):
    """Go decodes JSON numbers to float64; mirror that so %d/%v/T match."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, list):
        return [_to_go_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_go_numbers(item) for key, item in value.items()}
    return value


def render(template, data):
    """Render `template` against JSON-decoded `data`; exact `gh -t` output."""
    if template == "":
        return None  # gh treats an empty template as no template at all
    data = _to_go_numbers(data)
    colour = _colour_on()
    out = []
    try:
        tree = _Parser(template).parse()
        state = _State(data, colour, out)
        try:
            _execute(tree, state, out)
        except (_BreakSignal, _ContinueSignal):
            pass
        state.writer.flush_all()
        return "".join(out)
    except TemplateError as exc:
        message = str(exc)
        if not message.startswith("template:"):
            message = "template: :1: " + message
        raise TemplateError(message)
