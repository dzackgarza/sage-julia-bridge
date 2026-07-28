"""Bridge exception types, shared by the protocol and codec layers."""

from __future__ import annotations


class JuliaError(RuntimeError):
    """Base exception for the Julia bridge."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "backend",
        backend_type: str | None = None,
        backend_stack: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.backend_type = backend_type
        self.backend_stack = backend_stack


class JuliaProtocolError(JuliaError):
    """Raised when data crossing the bridge violates the wire format."""
