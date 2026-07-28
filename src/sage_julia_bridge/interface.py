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
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from collections import deque
from collections.abc import Iterator
from numbers import Integral, Rational
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sage.matrix.constructor import matrix
from sage.rings.integer_ring import ZZ
from sage.rings.rational_field import QQ
from sage.structure.element import Matrix, Vector

from sage_julia_bridge.errors import JuliaError, JuliaProtocolError
from sage_julia_bridge.mrdi import decode_mrdi, encode_mrdi

type StructuredValue = dict[str, Any]

_JULIA_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_!]*")


class BridgeResponse(BaseModel):
    """One response from the Julia worker."""

    model_config = ConfigDict(frozen=True)

    display: str
    structured: str
    stdout: str
    stderr: str


class JuliaHandle:
    """Opaque reference to a Julia value held in the worker process.

    Returned by sage()/call() for values the structured codec does not cover.
    Handles are valid as set()/call() inputs; sage() attempts explicit
    materialization and raises TypeError if the value is still uncovered.
    """

    def __init__(self, bridge: Julia, handle_id: int, julia_type: str, display: str) -> None:
        self._bridge = bridge
        self._id = handle_id
        self._generation = bridge._generation
        self._julia_type = julia_type
        self._display = display

    def __repr__(self) -> str:
        return f"JuliaHandle<{self._julia_type}>({self._display})"

    def _assert_current(self) -> None:
        # Ids restart with each worker process; a stale id would silently
        # resolve to a different object in the new worker's table.
        if self._generation != self._bridge._generation:
            raise JuliaError(f"stale handle from a previous Julia worker: {self!r}", kind="stale-object")

    def _operation(self, operation: str, **payload: object) -> Any:
        self._assert_current()
        response = self._bridge._request(
            "object",
            json.dumps(
                {
                    "op": operation,
                    "id": self._id,
                    **payload,
                }
            ),
        )
        return self._bridge._decode_value(response.structured, response.display)

    def sage(self) -> Any:
        self._assert_current()
        response = self._bridge._request("materialize", str(self._id))
        return self._bridge._decode_value(response.structured, response.display)

    def release(self) -> None:
        if self._generation == self._bridge._generation:
            self._bridge._request("release", str(self._id))

    def identity_key(self) -> tuple[str, int, int]:
        self._assert_current()
        return (self._bridge._session_id, self._generation, self._id)

    def getproperty(self, name: str) -> Any:
        return self._operation("getproperty", name=name)

    def setproperty(self, name: str, value: object) -> Any:
        return self._operation("setproperty", name=name, value=self._bridge._encode_value(value))

    def __call__(self, *args: object, **kwds: object) -> Any:
        self._assert_current()
        response = self._bridge._request(
            "call_object",
            json.dumps(
                {
                    "function": self._bridge._encode_value(self),
                    "args": [self._bridge._encode_value(arg) for arg in args],
                    "kwargs": {key: self._bridge._encode_value(value) for key, value in kwds.items()},
                }
            ),
        )
        return self._bridge._decode_value(response.structured, response.display)

    def __getitem__(self, index: object) -> Any:
        return self._operation("getindex", index=self._bridge._encode_value(index))

    def __iter__(self) -> Iterator[Any]:
        values = self._operation("iterate")
        return iter(values)

    def _binary(self, function: str, other: object) -> Any:
        return self._bridge.call(function, self, other)

    def __add__(self, other: object) -> Any:
        return self._binary("+", other)

    def __mul__(self, other: object) -> Any:
        return self._binary("*", other)

    def __truediv__(self, other: object) -> Any:
        return self._binary("/", other)

    def domain(self) -> Any:
        return self._bridge.call("domain", self)

    def codomain(self) -> Any:
        return self._bridge.call("codomain", self)

    def base_ring(self) -> Any:
        return self._bridge.call("base_ring", self)

    def parent(self) -> Any:
        return self._bridge.call("parent", self)

    def __del__(self) -> None:
        # Only enqueue: sending a request here could interleave with an
        # in-flight request on the same pipe (GC runs at arbitrary points).
        # Stale handles enqueue nothing: the value died with its worker.
        if self._generation == self._bridge._generation:
            self._bridge._pending_releases.append(self._id)


class Julia:
    """Minimal Julia bridge suitable for use from Sage."""

    def __init__(self, command: str | None = None) -> None:
        self._command = command or self._default_command()
        self._bridge = Path(__file__).with_name("julia_bridge.jl")
        self._session_id = str(uuid.uuid4())
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._stderr: deque[str] = deque(maxlen=200)
        self._stderr_thread: threading.Thread | None = None
        self._pending_releases: deque[int] = deque()
        self._generation = 0

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
        msg = "Julia executable not found; set SAGE_JULIA_COMMAND or install Julia via juliaup"
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
        # The bridge's own Julia deps (JSON) live in a repo-scoped project;
        # Julia's default load-path stacking keeps shared-env packages such
        # as Oscar loadable alongside it.
        julia_env = Path(__file__).with_name("julia_env")
        argv = self._command_argv() + [
            f"--project={julia_env}",
            "--startup-file=no",
            "--history-file=no",
            "--color=no",
            str(self._bridge),
        ]
        self._stderr.clear()
        # Handle ids die with the worker process they belong to.
        self._pending_releases.clear()
        self._generation += 1
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
            while self._pending_releases:
                handle_id = self._pending_releases.popleft()
                self._request_unlocked("release", str(handle_id))
            return self._request_unlocked(op, payload)

    def _request_unlocked(self, op: str, payload: str) -> BridgeResponse:
        assert self._proc is not None
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        try:
            self._proc.stdin.write(f"{op}\t{self._encode(payload)}\n")
            self._proc.stdin.flush()
        except BrokenPipeError as exc:
            self._mark_worker_dead()
            raise JuliaError(self._dead_process_message()) from exc

        line = self._proc.stdout.readline()
        if not line:
            self._mark_worker_dead()
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
            error_payload = self._decode(parts[1])
            stdout_text = self._decode(parts[2])
            stderr_text = self._decode(parts[3])
            error_data = json.loads(error_payload)
            message = self._merge_text(error_data["message"], stdout_text, stderr_text)
            raise JuliaError(
                message,
                kind=error_data["kind"],
                backend_type=error_data["backend_type"],
                backend_stack=error_data["backend_stack"],
            )
        raise JuliaProtocolError(f"unknown Julia response status: {status!r}")

    def _mark_worker_dead(self) -> None:
        proc = self._proc
        self._proc = None
        self._stderr_thread = None
        self._pending_releases.clear()
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        if proc is not None:
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(proc, stream_name)
                if stream is not None:
                    self._close_worker_stream(stream)

    def _close_worker_stream(self, stream: Any) -> None:
        try:
            stream.close()
        except BrokenPipeError as exc:
            self._stderr.append(f"worker stream close hit broken pipe: {exc}\n")

    def _dead_process_message(self) -> str:
        message = "Julia bridge process exited unexpectedly"
        stderr = self._stderr_tail()
        if stderr:
            return f"{message}\n{stderr}"
        return message

    def _merge_text(self, display: str, stdout: str, stderr: str) -> str:
        parts = [chunk.rstrip() for chunk in (stdout, stderr, display) if chunk.strip()]
        return "\n".join(parts)

    def _encode_value(self, value: object) -> StructuredValue:
        if value is None:
            return {"type": "nothing"}
        if isinstance(value, bool):
            return {"type": "bool", "value": value}
        if isinstance(value, str):
            return {"type": "string", "value": value}
        # Parent-carrying algebra travels as mrdi. This runs before the
        # numeric branches so e.g. GF(p) elements can never be flattened
        # to bare integers by a numbers-ABC registration.
        mrdi_doc = encode_mrdi(value)
        if mrdi_doc is not None:
            return {"type": "mrdi", "data": mrdi_doc}
        if isinstance(value, Integral):
            return {"type": "int", "value": str(int(value))}
        if isinstance(value, Rational):
            numerator = value.numerator
            denominator = value.denominator
            if callable(numerator):
                numerator = numerator()
            if callable(denominator):
                denominator = denominator()
            return {
                "type": "rational",
                "num": str(int(numerator)),
                "den": str(int(denominator)),
            }
        if isinstance(value, JuliaHandle):
            assert value._bridge is self, "handle belongs to a different Julia bridge"
            value._assert_current()
            return {"type": "handle", "id": value._id}
        if isinstance(value, (Vector, list, tuple)):
            return {
                "type": "vector",
                "data": [self._encode_value(entry) for entry in value],
            }
        if isinstance(value, Matrix):
            entries = [self._encode_value(value[i, j]) for i in range(value.nrows()) for j in range(value.ncols())]
            return {
                "type": "matrix",
                "nrows": value.nrows(),
                "ncols": value.ncols(),
                "data": entries,
            }
        msg = f"unsupported Julia bridge input type: {type(value).__name__}; use eval(...) with Julia source for values outside the structured codec"
        raise TypeError(msg)

    def _decode_value(self, payload: str | StructuredValue, display: str) -> Any:
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
            # Containers are containers (docs/wire-format.md).
            return [self._decode_value(item, display) for item in data["data"]]
        if kind == "matrix":
            entries = [self._decode_value(item, display) for item in data["data"]]
            return matrix(data["nrows"], data["ncols"], entries)
        if kind == "mrdi":
            return decode_mrdi(data["data"])
        if kind == "handle":
            return JuliaHandle(self, data["id"], data["julia_type"], data["display"])
        if kind == "unsupported":
            julia_type = data["julia_type"]
            msg = f"cannot convert Julia value of type {julia_type} to Sage; use eval(...) instead\n{display}"
            raise TypeError(msg)
        raise JuliaProtocolError(f"unknown Julia value type: {kind!r}")

    def eval(self, code: str) -> str:
        response = self._request("exec", code)
        return self._merge_text(response.display, response.stdout, response.stderr)

    def sage(self, code: str) -> Any:
        response = self._request("value", code)
        return self._decode_value(response.structured, response.display)

    def __call__(self, code: str) -> Any:
        return self.sage(code)

    def set(self, var: str, value: object) -> None:
        assert _JULIA_IDENTIFIER.fullmatch(var), f"invalid Julia variable name: {var!r}"
        payload = json.dumps({"name": var, "value": self._encode_value(value)})
        self._request("set", payload)

    def get(self, var: str) -> str:
        return self.eval(var)

    def get_sage(self, var: str) -> Any:
        return self.sage(var)

    def call(self, function: str, *args: object, **kwds: object) -> Any:
        payload = json.dumps(
            {
                "function": function,
                "args": [self._encode_value(arg) for arg in args],
                "kwargs": {key: self._encode_value(value) for key, value in kwds.items()},
            }
        )
        response = self._request("call", payload)
        return self._decode_value(response.structured, response.display)

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
                        self._close_worker_stream(stream)
                self._proc = None
                self._stderr_thread = None


julia = Julia()

atexit.register(julia.quit)
