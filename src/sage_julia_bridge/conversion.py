"""Extensible conversion registry for Sage/Julia bridge values."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

from sage_julia_bridge.errors import JuliaConversionError

type StructuredValue = dict[str, Any]
type SageEncoder = Callable[[object, Callable[[object], StructuredValue]], StructuredValue | None]
type SageMaterializer = Callable[[Any], Any]


@dataclass(frozen=True)
class RegisteredSageEncoder:
    """Predicate-directed conversion from a Sage/Python object into Julia data."""

    predicate: Callable[[object], bool]
    encoder: SageEncoder


class ConversionRegistry:
    """Target-directed conversion hooks above the generic retained-object runtime."""

    def __init__(self) -> None:
        self._sage_encoders: list[RegisteredSageEncoder] = []
        self._materializers: dict[Hashable, SageMaterializer] = {}

    def register_to_julia(self, predicate: Callable[[object], bool], encoder: SageEncoder) -> None:
        """Register an outbound Sage/Python -> Julia encoder."""

        self._sage_encoders.append(RegisteredSageEncoder(predicate, encoder))

    def encode_to_julia(
        self,
        value: object,
        builtin_encoder: Callable[[object], StructuredValue],
    ) -> StructuredValue:
        """Encode through the first matching extension, then the builtin policy."""

        for registered in reversed(self._sage_encoders):
            if registered.predicate(value):
                encoded = registered.encoder(value, builtin_encoder)
                if encoded is None:
                    break
                return encoded
        return builtin_encoder(value)

    def register_to_sage(self, target: Hashable, materializer: SageMaterializer) -> None:
        """Register a target-directed Julia handle -> Sage materializer."""

        self._materializers[target] = materializer

    def convert_to_sage(self, handle: Any, target: Hashable) -> Any:
        """Materialize ``handle`` as ``target`` or raise typed refusal."""

        materializer = self._materializers.get(target)
        if materializer is None:
            raise JuliaConversionError(
                f"no Sage conversion registered for Julia value of type {handle.julia_type()} to target {target!r}",
                target=target,
                julia_type=handle.julia_type(),
            )
        return materializer(handle)
