"""Flag parsing that behaves like gh's pflag surface.

gh accepts `--flag value`, `--flag=value`, `-f value`, `-fvalue`, repeated
list flags, and `--` to end flags. Anything it does not know is a usage error
naming the flag, so scripts notice the same mistakes they would with gh.
"""

from __future__ import annotations

import os

from .errors import UsageError


class Spec:
    def __init__(self):
        self.booleans = {}     # name -> [aliases]
        self.values = {}       # name -> [aliases]
        self.lists = {}        # name -> [aliases]
        self.aliases = {}      # alias -> canonical

    def flag(self, name, *aliases):
        self.booleans[name] = list(aliases)
        for alias in aliases:
            self.aliases[alias] = name
        return self

    def value(self, name, *aliases):
        self.values[name] = list(aliases)
        for alias in aliases:
            self.aliases[alias] = name
        return self

    def list(self, name, *aliases):
        self.lists[name] = list(aliases)
        for alias in aliases:
            self.aliases[alias] = name
        return self

    def canonical(self, token):
        if token in self.aliases:
            return self.aliases[token]
        return token

    def kinds(self):
        table = {}
        for name in self.booleans:
            table[name] = "bool"
        for name in self.values:
            table[name] = "value"
        for name in self.lists:
            table[name] = "list"
        return table


def split(argv, spec):
    """Return (options, positionals) without touching the process state."""
    kinds = spec.kinds()
    options = {}
    positionals = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            positionals.extend(argv[index + 1:])
            break
        if token.startswith("--") and len(token) > 2:
            name, _, inline = token[2:].partition("=")
            name = spec.canonical(name)
            kind = kinds.get(name)
            if kind is None:
                raise UnknownFlag("--" + name)
            if kind == "bool":
                if inline:
                    options[name] = _truthy(inline)
                else:
                    options[name] = True
                index += 1
                continue
            if inline:
                value = inline
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise UsageError("flag needs an argument: --" + name)
                value = argv[index + 1]
                index += 2
            if kind == "list":
                options.setdefault(name, []).append(value)
            else:
                options[name] = value
            continue
        if token.startswith("-") and token not in ("-",) and len(token) > 1:
            body = token[1:]
            name = body[0]
            name = spec.canonical(name)
            kind = kinds.get(name)
            if kind is None:
                raise UnknownFlag("-" + body[0])
            rest = body[1:]
            if kind == "bool":
                options[name] = True
                index += 1
                continue
            if rest:
                value = rest
                index += 1
            else:
                if index + 1 >= len(argv):
                    raise UsageError("flag needs an argument: -" + body[0])
                value = argv[index + 1]
                index += 2
            if kind == "list":
                options.setdefault(name, []).append(value)
            else:
                options[name] = value
            continue
        positionals.append(token)
        index += 1
    return options, positionals


class UnknownFlag(UsageError):
    def __init__(self, flag, usage=None):
        self.flag = flag
        self.usage = usage
        super().__init__("unknown flag: " + flag)


def _truthy(text):
    return str(text).lower() not in ("false", "0", "no")


def env_repo():
    return os.environ.get("GH_REPO") or None


# Reusable flag groups, named the way gh documents them.
def json_flags(spec):
    spec.value("json")
    spec.value("jq", "q")
    spec.value("template", "t")
    return spec


def limit_flag(spec, default=30):
    spec.value("limit", "L")
    return default


def repo_flag(spec):
    spec.value("repo", "R")
    return spec
