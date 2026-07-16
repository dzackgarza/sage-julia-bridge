"""Bridge exception types, shared by the protocol and codec layers."""

from __future__ import annotations


class JuliaError(RuntimeError):
    """Base exception for the Julia bridge."""


class JuliaProtocolError(JuliaError):
    """Raised when data crossing the bridge violates the wire format."""
