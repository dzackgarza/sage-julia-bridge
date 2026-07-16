"""
Standalone Julia bridge for Sage.

This package keeps Julia in a separate long-lived process and communicates over
an intentionally small line-based protocol.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import shlex
import shutil
import subprocess
import threading
from collections import deque
from numbers import Integral, Rational
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sage.matrix.constructor import matrix
from sage.modules.free_module_element import vector
from sage.rings.integer_ring import ZZ
from sage.rings.rational_field import QQ
from sage.structure.element import Matrix, Vector

type StructuredValue = dict[str, object]


class JuliaError(RuntimeError):
    """Base exception for the Julia bridge."""


class JuliaProtocolError(JuliaError):
    """Raised when the Julia bridge returns malformed data."""


class BridgeResponse(BaseModel):
    """One response from the Julia worker."""

    model_config = ConfigDict(frozen=True)

    display: str
    structured: str
    stdout: str
    stderr: str


class Julia:
    """Minimal Julia bridge suitable for use from Sage."""

    def __init__(self, command: str | None = None) -> None:
        self._command = command or self._default_command()
        self._bridge = Path(__file__).with_name("julia_bridge.jl")
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._stderr: deque[str] = deque(maxlen=200)
        self._stderr_thread: threading.Thread | None = None

    def __repr__(self) -> str:
        return "Julia"

    def __enter__(self) -> Julia:
        self._ensure_process()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.quit()

    def _default_command(self) -> str:
        for name in ("SAGE_JULIA_COMMAND", "JULIA_COMMAND"):
            command = os.environ.get(name)
            if command:
                return command
        juliaup = Path.home() / ".juliaup" / "bin" / "julia"
        if juliaup.exists():
            return str(juliaup)
        command = shutil.which("julia")
        if command:
            return command
        msg = (
            "Julia executable not found; "
            "set SAGE_JULIA_COMMAND or install Julia via juliaup"
        )
        raise JuliaError(msg)

    def _command_argv(self) -> list[str]:
        return shlex.split(self._command)

    def _drain_stderr(self) -> None:
        assert self._proc is not None
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.append(line)

    def _stderr_tail(self) -> str:
        return "".join(self._stderr).strip()

    def _ensure_process(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        argv = self._command_argv() + [
            "--startup-file=no",
            "--history-file=no",
            "--color=no",
            str(self._bridge),
        ]
        self._stderr.clear()
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._request_unlocked("ping", "")

    def _encode(self, value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def _decode(self, value: str) -> str:
        if not value:
            return ""
        return base64.b64decode(value.encode("ascii")).decode("utf-8")

    def _request(self, op: str, payload: str) -> BridgeResponse:
        with self._lock:
            self._ensure_process()
            return self._request_unlocked(op, payload)

    def _request_unlocked(self, op: str, payload: str) -> BridgeResponse:
        assert self._proc is not None
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        try:
            self._proc.stdin.write(f"{op}\t{self._encode(payload)}\n")
            self._proc.stdin.flush()
        except BrokenPipeError as exc:
            raise JuliaError(self._dead_process_message()) from exc

        line = self._proc.stdout.readline()
        if not line:
            raise JuliaError(self._dead_process_message())

        parts = line.rstrip("\n").split("\t")
        status = parts[0]
        if status == "ok":
            if len(parts) != 5:
                raise JuliaProtocolError(f"malformed Julia response: {line!r}")
            return BridgeResponse(
                display=self._decode(parts[1]),
                structured=self._decode(parts[2]),
                stdout=self._decode(parts[3]),
                stderr=self._decode(parts[4]),
            )
        if status == "err":
            if len(parts) != 4:
                raise JuliaProtocolError(f"malformed Julia error response: {line!r}")
            raise JuliaError(
                self._merge_text(
                    self._decode(parts[1]),
                    self._decode(parts[2]),
                    self._decode(parts[3]),
                )
            )
        raise JuliaProtocolError(f"unknown Julia response status: {status!r}")

    def _dead_process_message(self) -> str:
        message = "Julia bridge process exited unexpectedly"
        stderr = self._stderr_tail()
        if stderr:
            return f"{message}\n{stderr}"
        return message

    def _merge_text(self, display: str, stdout: str, stderr: str) -> str:
        parts = [chunk.rstrip() for chunk in (stdout, stderr, display) if chunk.strip()]
        return "\n".join(parts)

    def _to_julia_literal(self, value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, Integral):
            return str(int(value))
        if isinstance(value, Rational):
            numerator = value.numerator
            denominator = value.denominator
            if callable(numerator):
                numerator = numerator()
            if callable(denominator):
                denominator = denominator()
            return f"{int(numerator)}//{int(denominator)}"
        if isinstance(value, Vector):
            return (
                "[" + ", ".join(self._to_julia_literal(entry) for entry in value) + "]"
            )
        if isinstance(value, Matrix):
            rows: list[str] = []
            for i in range(value.nrows()):
                row = " ".join(
                    self._to_julia_literal(value[i, j]) for j in range(value.ncols())
                )
                rows.append(row)
            return "[" + "; ".join(rows) + "]"
        if isinstance(value, list):
            return (
                "[" + ", ".join(self._to_julia_literal(entry) for entry in value) + "]"
            )
        if isinstance(value, tuple):
            return (
                "[" + ", ".join(self._to_julia_literal(entry) for entry in value) + "]"
            )
        raise TypeError(f"unsupported Julia bridge input type: {type(value).__name__}")

    def _decode_value(self, payload: str | StructuredValue, display: str) -> object:
        data = json.loads(payload) if isinstance(payload, str) else payload
        kind = data["type"]
        if kind == "nothing":
            return None
        if kind == "bool":
            return data["value"]
        if kind == "string":
            return data["value"]
        if kind == "int":
            return ZZ(data["value"])
        if kind == "rational":
            return QQ(ZZ(data["num"])) / QQ(ZZ(data["den"]))
        if kind == "vector":
            return vector([self._decode_value(item, display) for item in data["data"]])
        if kind == "matrix":
            entries = [self._decode_value(item, display) for item in data["data"]]
            return matrix(data["nrows"], data["ncols"], entries)
        if kind == "unsupported":
            julia_type = data["julia_type"]
            msg = (
                f"cannot convert Julia value of type {julia_type} to Sage; "
                f"use eval(...) instead\n{display}"
            )
            raise TypeError(msg)
        raise JuliaProtocolError(f"unknown Julia value type: {kind!r}")

    def eval(self, code: str) -> str:
        response = self._request("exec", code)
        return self._merge_text(response.display, response.stdout, response.stderr)

    def sage(self, code: str) -> object:
        response = self._request("exec", code)
        return self._decode_value(response.structured, response.display)

    def __call__(self, code: str) -> object:
        return self.sage(code)

    def set(self, var: str, value: object) -> None:
        self.eval(f"{var} = {self._to_julia_literal(value)}")

    def get(self, var: str) -> str:
        return self.eval(var)

    def get_sage(self, var: str) -> object:
        return self.sage(var)

    def call(self, function: str, *args: object, **kwds: object) -> object:
        arguments = [self._to_julia_literal(arg) for arg in args]
        arguments.extend(
            f"{key}={self._to_julia_literal(value)}" for key, value in kwds.items()
        )
        return self.sage(f"{function}({', '.join(arguments)})")

    def version(self) -> str:
        return self.eval("VERSION")

    def quit(self) -> None:
        with self._lock:
            if self._proc is None:
                return
            try:
                if self._proc.poll() is None:
                    try:
                        self._request_unlocked("quit", "")
                        self._proc.wait(timeout=2)
                    except Exception:
                        try:
                            self._proc.terminate()
                            self._proc.wait(timeout=2)
                        except Exception:
                            self._proc.kill()
                            self._proc.wait(timeout=2)
            finally:
                for stream_name in ("stdin", "stdout", "stderr"):
                    stream = getattr(self._proc, stream_name)
                    if stream is not None:
                        stream.close()
                self._proc = None
                self._stderr_thread = None


julia = Julia()

atexit.register(julia.quit)
