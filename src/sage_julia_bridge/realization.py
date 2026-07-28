"""Explicit Sage-Oscar realization maps above the generic object runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sage.categories.homset import Hom
from sage.rings.morphism import RingHomomorphism

from sage_julia_bridge.errors import JuliaConversionError
from sage_julia_bridge.interface import JuliaHandle

type RealizationMaterializer = Callable[[Any, JuliaHandle], Any]


def parent_of(value: Any) -> Any | None:
    """Return the Sage parent of ``value`` when it has one."""

    if not hasattr(value, "parent"):
        return None
    parent = value.parent
    if not callable(parent):
        raise JuliaConversionError(
            "Sage value exposes a non-callable parent attribute",
            kind="parent-incompatible",
        )
    return parent()


def coerce_compatible_parent(domain: Any, value: Any) -> Any:
    """Coerce ``value`` into ``domain`` only through an explicit Sage parent edge."""

    source_parent = parent_of(value)
    if source_parent is not None and source_parent is not domain:
        if not hasattr(domain, "has_coerce_map_from"):
            raise JuliaConversionError(
                "Sage-Oscar realization domain has no parent compatibility predicate",
                target=domain,
                kind="parent-incompatible",
            )
        has_coerce_map_from = domain.has_coerce_map_from
        if not callable(has_coerce_map_from):
            raise JuliaConversionError(
                "Sage-Oscar realization domain exposes a non-callable parent compatibility predicate",
                target=domain,
                kind="parent-incompatible",
            )
        if not has_coerce_map_from(source_parent):
            raise JuliaConversionError(
                "value parent is not compatible with the Sage-Oscar realization domain",
                target=domain,
                kind="parent-incompatible",
            )
    try:
        return domain(value)
    except (TypeError, ValueError) as exc:
        raise JuliaConversionError(
            "value cannot be coerced into the Sage-Oscar realization domain",
            target=domain,
            kind="parent-incompatible",
        ) from exc


class SageOscarRealizationMap(RingHomomorphism):
    """Native Sage ring morphism retaining the corresponding Oscar map."""

    def __init__(
        self,
        domain: Any,
        codomain: Any,
        oscar_map: JuliaHandle,
        materializer: RealizationMaterializer,
    ) -> None:
        RingHomomorphism.__init__(self, Hom(domain, codomain))
        self._oscar_map = oscar_map
        self._materializer = materializer

    def _call_(self, value: Any) -> Any:
        coerced = coerce_compatible_parent(self.domain(), value)
        oscar_value = self._oscar_map(coerced)
        return self._materializer(coerced, oscar_value)

    def __call__(self, value: Any) -> Any:
        return self._call_(value)

    def oscar(self) -> JuliaHandle:
        return self._oscar_map
