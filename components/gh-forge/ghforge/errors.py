"""Errors the CLI knows how to render the way `gh` renders them."""

from __future__ import annotations


class GhError(Exception):
    """An error that ends the run with `gh: <message>` on stderr."""

    exit_code = 1

    def __init__(self, message, *, hint=None, body=None, status=None, bare=False):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.body = body
        self.status = status
        # jq's own errors reach stderr the way gojq wrote them: no prefix.
        self.bare = bare


class UsageError(GhError):
    """Bad flags or arguments: `gh` prints the message alone, exit 1."""

    exit_code = 1


class Unsupported(GhError):
    """A command or flag the read-only fallback cannot honestly answer."""


class AuthMissing(GhError):
    """Nothing usable: neither a working `gh` nor a reachable forge."""


class NetworkError(GhError):
    """The forge could not be reached, or answered outside the contract."""
