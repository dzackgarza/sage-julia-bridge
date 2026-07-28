"""Native Sage facades for Oscar-backed prime localizations."""

from __future__ import annotations

from typing import Any, cast

from sage.categories.rings import Rings
from sage.structure.element import Element
from sage.structure.parent import Parent

from sage_julia_bridge.errors import JuliaConversionError
from sage_julia_bridge.interface import JuliaHandle, julia
from sage_julia_bridge.realization import SageOscarRealizationMap, coerce_compatible_parent


class PrimeLocalizationElement(Element):
    """Element of a prime-local Sage facade backed by an Oscar object."""

    def __init__(
        self,
        parent: PrimeLocalizationParent,
        numerator: Any,
        denominator: Any | None = None,
        *,
        oscar_value: JuliaHandle | None = None,
    ) -> None:
        super().__init__(parent)
        self._numerator = coerce_compatible_parent(parent._base, numerator)
        self._denominator = coerce_compatible_parent(parent._base, 1 if denominator is None else denominator)
        parent._assert_valid_denominator(self._denominator)
        self._fraction = parent._fraction_field(self._numerator) / parent._fraction_field(self._denominator)
        self._oscar: JuliaHandle = (
            oscar_value if oscar_value is not None and self._denominator == parent._base(1) else parent._iota_oscar(self._numerator) / parent._iota_oscar(self._denominator)
        )

    def _repr_(self) -> str:
        if self._denominator == self.parent()._base(1):
            return repr(self._numerator)
        return f"({self._numerator})/({self._denominator})"

    def oscar(self) -> JuliaHandle:
        return self._oscar

    def numerator(self) -> Any:
        return self._numerator

    def denominator(self) -> Any:
        return self._denominator

    def is_unit(self) -> bool:
        return self._numerator not in self.parent()._prime

    def _add_(self, other: PrimeLocalizationElement) -> PrimeLocalizationElement:
        parent = self.parent()
        numerator = self._numerator * other._denominator + other._numerator * self._denominator
        denominator = self._denominator * other._denominator
        return cast(PrimeLocalizationElement, parent(numerator, denominator))

    def _mul_(self, other: PrimeLocalizationElement) -> PrimeLocalizationElement:
        return cast(
            PrimeLocalizationElement,
            self.parent()(self._numerator * other._numerator, self._denominator * other._denominator),
        )

    def __truediv__(self, other: Any) -> PrimeLocalizationElement:
        rhs = self.parent()(other)
        self.parent()._assert_valid_denominator(rhs._numerator)
        return cast(
            PrimeLocalizationElement,
            self.parent()(self._numerator * rhs._denominator, self._denominator * rhs._numerator),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PrimeLocalizationElement):
            return False
        return self.parent() is other.parent() and self._fraction == other._fraction


class PrimeLocalizationIdeal:
    """Ideal of a prime-local Sage facade."""

    def __init__(self, ring: PrimeLocalizationParent, generators: list[Any]) -> None:
        self._ring = ring
        self._generators = tuple(ring(generator) for generator in generators)

    def ring(self) -> PrimeLocalizationParent:
        return self._ring

    def gens(self) -> tuple[PrimeLocalizationElement, ...]:
        return self._generators

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PrimeLocalizationIdeal) and self._ring is other._ring and self._generators == other._generators

    def _repr_(self) -> str:
        return f"Ideal {self._generators} of {self._ring}"


class PrimeLocalizationParent(Parent):
    """Prime-local Sage parent with retained Oscar localization access."""

    Element = PrimeLocalizationElement

    def __init__(self, base: Any, prime: Any, oscar_ring: JuliaHandle, oscar_iota: JuliaHandle) -> None:
        Parent.__init__(self, base=base, category=Rings())
        self._base = base
        self._prime = prime
        self._oscar_ring = oscar_ring
        self._oscar_iota = oscar_iota
        self._fraction_field = base.fraction_field()
        self._maximal_ideal = PrimeLocalizationIdeal(self, list(prime.gens()))

    def _repr_(self) -> str:
        return f"{self._base} localized at {self._prime}"

    def _element_constructor_(self, numerator: Any, denominator: Any | None = None) -> PrimeLocalizationElement:
        if isinstance(numerator, PrimeLocalizationElement):
            if numerator.parent() is not self:
                raise JuliaConversionError(
                    "cannot coerce an element from an incompatible prime localization",
                    target=self,
                    kind="parent-incompatible",
                )
            if denominator is not None:
                return cast(
                    PrimeLocalizationElement,
                    self(numerator._numerator, numerator._denominator * self._base(denominator)),
                )
            return numerator
        return PrimeLocalizationElement(self, numerator, denominator)

    def _from_base_realization(self, value: Any, oscar_value: JuliaHandle) -> PrimeLocalizationElement:
        return PrimeLocalizationElement(self, value, oscar_value=oscar_value)

    def _assert_valid_denominator(self, denominator: Any) -> None:
        if denominator in self._prime:
            raise ZeroDivisionError("denominator lies in the localized prime ideal")

    def _iota_oscar(self, value: Any) -> JuliaHandle:
        return cast(JuliaHandle, self._oscar_iota(value))

    def oscar(self) -> JuliaHandle:
        return self._oscar_ring

    def ideal(self, generators: Any, *extra: Any) -> PrimeLocalizationIdeal:
        if extra:
            generator_list = [generators, *extra]
        elif isinstance(generators, (list, tuple)):
            generator_list = list(generators)
        else:
            generator_list = [generators]
        return PrimeLocalizationIdeal(self, generator_list)

    def maximal_ideal(self) -> PrimeLocalizationIdeal:
        return self._maximal_ideal

    def residue_field(self) -> tuple[Any, PrimeLocalizationResidueMap]:
        quotient = self._base.quotient(self._prime)
        field = quotient.fraction_field()
        return field, PrimeLocalizationResidueMap(self, field, quotient)


class PrimeLocalizationMap(SageOscarRealizationMap):
    """Sage-callable localization map retaining the Oscar map."""

    def __init__(self, domain: Any, codomain: PrimeLocalizationParent, oscar_map: JuliaHandle) -> None:
        super().__init__(domain, codomain, oscar_map, codomain._from_base_realization)


class PrimeLocalizationResidueMap:
    """Residue map from a prime localization to the fraction field of R/P."""

    def __init__(self, domain: PrimeLocalizationParent, codomain: Any, quotient: Any) -> None:
        self._domain = domain
        self._codomain = codomain
        self._quotient = quotient

    def domain(self) -> PrimeLocalizationParent:
        return self._domain

    def codomain(self) -> Any:
        return self._codomain

    def __call__(self, value: Any) -> Any:
        element = self._domain(value)
        numerator = self._quotient(element.numerator())
        denominator = self._quotient(element.denominator())
        return self._codomain(numerator) / self._codomain(denominator)


def prime_localization(base: Any, prime: Any) -> tuple[PrimeLocalizationParent, PrimeLocalizationMap]:
    """Construct an Oscar-backed Sage facade for the localization at ``prime``."""

    if not prime.is_prime():
        raise ValueError("prime_localization requires a prime ideal")
    if prime.ring() is not base:
        raise TypeError("prime ideal must belong to the given base ring")

    julia.eval("using Oscar")
    oscar_prime = julia.call("ideal", base, list(prime.gens()))
    oscar_units = julia.call("complement_of_prime_ideal", oscar_prime, check=False)
    oscar_ring, oscar_iota = julia.call("localization", base, oscar_units)
    localization = PrimeLocalizationParent(base, prime, oscar_ring, oscar_iota)
    return localization, PrimeLocalizationMap(base, localization, oscar_iota)
