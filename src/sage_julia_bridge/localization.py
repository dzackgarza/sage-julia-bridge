"""Native Sage facades for Oscar-backed prime localizations."""

from __future__ import annotations

from typing import Any, cast

from sage.categories.category_singleton import Category_singleton
from sage.categories.fields import Fields
from sage.categories.homset import Hom
from sage.categories.integral_domains import IntegralDomains
from sage.rings.ideal import Ideal_generic
from sage.rings.morphism import RingHomomorphism
from sage.structure.element import FieldElement, IntegralDomainElement
from sage.structure.parent import Parent

from sage_julia_bridge.errors import JuliaConversionError
from sage_julia_bridge.interface import JuliaHandle, julia
from sage_julia_bridge.realization import SageOscarRealizationMap, coerce_compatible_parent


class PrimeLocalRings(Category_singleton):
    """Category of integral domains with one maximal ideal."""

    def super_categories(self) -> list[Any]:
        return [IntegralDomains()]

    def _repr_object_names(self) -> str:
        return "prime local rings"


class PrimeLocalizationElement(IntegralDomainElement):
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

    def inverse(self) -> PrimeLocalizationElement:
        if not self.is_unit():
            raise ZeroDivisionError("nonunit prime-local element is not invertible")
        return cast(PrimeLocalizationElement, self.parent()(self._denominator, self._numerator))

    def __invert__(self) -> PrimeLocalizationElement:
        return self.inverse()

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

    def __rtruediv__(self, other: Any) -> PrimeLocalizationElement:
        return cast(PrimeLocalizationElement, self.parent()(other) / self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PrimeLocalizationElement):
            return False
        return self.parent() is other.parent() and self._fraction == other._fraction


class PrimeLocalizationIdeal(Ideal_generic):
    """Native Sage ideal retaining the corresponding Oscar localized ideal."""

    def __init__(self, ring: PrimeLocalizationParent, generators: list[Any]) -> None:
        from sage.all import ZZ

        Ideal_generic.__init__(self, ring, generators)
        self._base_ideal = ring._base.ideal([generator.numerator() for generator in self.gens()])
        constructor = "bridge_localized_ideal" if ring._base is ZZ else "ideal"
        self._oscar = cast(
            JuliaHandle,
            julia.call(constructor, ring.oscar(), [generator.oscar() for generator in self.gens()]),
        )

    def oscar(self) -> JuliaHandle:
        return self._oscar

    def is_maximal(self) -> bool:
        return bool(self == self.ring().maximal_ideal())

    def quotient(self) -> Any:
        return self.ring().quotient(self)

    def _contains_(self, value: Any) -> bool:
        element = self.ring()(value)
        if self.is_maximal():
            return element.numerator() in self.ring()._prime
        return element.numerator() in self._base_ideal

    def _richcmp_(self, other: Any, op: int) -> bool:
        if not isinstance(other, PrimeLocalizationIdeal) or self.ring() is not other.ring():
            return op == 3
        equal = self._oscar.backend_equals(other._oscar)
        return equal if op == 2 else not equal if op == 3 else False


class PrimeLocalizationParent(Parent):
    """Prime-local Sage parent with retained Oscar localization access."""

    Element = PrimeLocalizationElement

    def __init__(self, base: Any, prime: Any, oscar_ring: JuliaHandle, oscar_iota: JuliaHandle) -> None:
        Parent.__init__(self, base=base, category=PrimeLocalRings())
        self._base = base
        self._prime = prime
        self._oscar_ring = oscar_ring
        self._oscar_iota = oscar_iota
        self._fraction_field = base.fraction_field()
        self._maximal_ideal = PrimeLocalizationIdeal(self, list(prime.gens()))
        self._residue_field_data: tuple[Any, PrimeLocalizationResidueMap] | None = None
        self._populate_coercion_lists_()

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

    def fraction_field(self) -> Any:
        return self._fraction_field

    def quotient(self, ideal: PrimeLocalizationIdeal) -> Any:
        if ideal.ring() is not self:
            raise JuliaConversionError(
                "cannot quotient a prime localization by an ideal over another parent",
                target=self,
                kind="parent-incompatible",
            )
        if ideal == self.maximal_ideal():
            return self.residue_field()[0]
        raise NotImplementedError("only the maximal ideal quotient is currently implemented")

    def residue_field(self) -> tuple[Any, PrimeLocalizationResidueMap]:
        if self._residue_field_data is not None:
            return self._residue_field_data
        quotient = self._base.quotient(self._prime)
        native_field = quotient.fraction_field()
        oscar_field, oscar_map = _oscar_residue_realization(self)
        field = PrimeLocalizationResidueField(native_field, oscar_field)
        self._residue_field_data = (
            field,
            PrimeLocalizationResidueMap(self, field, quotient, oscar_map),
        )
        return self._residue_field_data


class PrimeLocalizationMap(SageOscarRealizationMap):
    """Sage-callable localization map retaining the Oscar map."""

    def __init__(self, domain: Any, codomain: PrimeLocalizationParent, oscar_map: JuliaHandle) -> None:
        super().__init__(domain, codomain, oscar_map, codomain._from_base_realization)


class PrimeLocalizationResidueFieldElement(FieldElement):
    """Element of the native residue-field facade."""

    def __init__(self, parent: PrimeLocalizationResidueField, value: Any) -> None:
        FieldElement.__init__(self, parent)
        self._value = parent._native(value)

    def _repr_(self) -> str:
        return repr(self._value)

    def _add_(self, other: PrimeLocalizationResidueFieldElement) -> PrimeLocalizationResidueFieldElement:
        return cast(PrimeLocalizationResidueFieldElement, self.parent()(self._value + other._value))

    def _mul_(self, other: PrimeLocalizationResidueFieldElement) -> PrimeLocalizationResidueFieldElement:
        return cast(PrimeLocalizationResidueFieldElement, self.parent()(self._value * other._value))

    def _neg_(self) -> PrimeLocalizationResidueFieldElement:
        return cast(PrimeLocalizationResidueFieldElement, self.parent()(-self._value))

    def __invert__(self) -> PrimeLocalizationResidueFieldElement:
        return cast(PrimeLocalizationResidueFieldElement, self.parent()(~self._value))

    def _div_(
        self,
        other: PrimeLocalizationResidueFieldElement,
    ) -> PrimeLocalizationResidueFieldElement:
        return cast(PrimeLocalizationResidueFieldElement, self.parent()(self._value / other._value))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PrimeLocalizationResidueFieldElement) and self.parent() is other.parent() and self._value == other._value


class PrimeLocalizationResidueField(Parent):
    """Native Sage field parent retaining the Oscar residue-field realization."""

    Element = PrimeLocalizationResidueFieldElement

    def __init__(self, native: Any, oscar_field: JuliaHandle) -> None:
        Parent.__init__(self, base=native.base_ring(), category=Fields())
        self._native = native
        self._oscar = oscar_field
        self._populate_coercion_lists_()

    def _repr_(self) -> str:
        return repr(self._native)

    def _element_constructor_(self, value: Any) -> PrimeLocalizationResidueFieldElement:
        if isinstance(value, PrimeLocalizationResidueFieldElement):
            if value.parent() is self:
                return value
            value = value._value
        return PrimeLocalizationResidueFieldElement(self, value)

    def oscar(self) -> JuliaHandle:
        return self._oscar

    def characteristic(self) -> Any:
        return self._native.characteristic()

    def is_finite(self) -> bool:
        return bool(self._native.is_finite())

    def order(self) -> Any:
        return self._native.order()

    def gen(self, index: int = 0) -> PrimeLocalizationResidueFieldElement:
        return cast(PrimeLocalizationResidueFieldElement, self(self._native.gen(index)))

    def gens(self) -> tuple[PrimeLocalizationResidueFieldElement, ...]:
        return tuple(self(generator) for generator in self._native.gens())


class PrimeLocalizationResidueMap(RingHomomorphism):
    """Native Sage residue morphism retaining the Oscar residue map."""

    def __init__(
        self,
        domain: PrimeLocalizationParent,
        codomain: PrimeLocalizationResidueField,
        quotient: Any,
        oscar_map: JuliaHandle,
    ) -> None:
        RingHomomorphism.__init__(self, Hom(domain, codomain))
        self._quotient = quotient
        self._oscar = oscar_map

    def _call_(self, value: Any) -> Any:
        element = self.domain()(value)
        numerator = self._quotient(element.numerator())
        denominator = self._quotient(element.denominator())
        return self.codomain()(numerator) / self.codomain()(denominator)

    def oscar(self) -> JuliaHandle:
        return self._oscar

    def kernel(self) -> PrimeLocalizationIdeal:
        return cast(PrimeLocalizationIdeal, self.domain().maximal_ideal())

    def is_surjective(self) -> bool:
        return True


def _oscar_residue_realization(
    localization: PrimeLocalizationParent,
) -> tuple[JuliaHandle, JuliaHandle]:
    from sage.all import ZZ

    if localization._base is ZZ:
        oscar_field, base_residue_map = julia.call(
            "residue_field",
            localization._base,
            localization._prime.gen(),
        )
        oscar_map = julia.call(
            "bridge_fraction_residue_map",
            localization.oscar(),
            oscar_field,
            base_residue_map,
        )
        return cast(JuliaHandle, oscar_field), cast(JuliaHandle, oscar_map)

    oscar_prime = julia.call(
        "ideal",
        localization._base,
        list(localization._prime.gens()),
    )
    quotient_ring, quotient_map = julia.call("quo", localization._base, oscar_prime)
    oscar_field = julia.call("fraction_field", quotient_ring)
    oscar_map = julia.call(
        "bridge_fraction_residue_map",
        localization.oscar(),
        oscar_field,
        quotient_map,
    )
    return cast(JuliaHandle, oscar_field), cast(JuliaHandle, oscar_map)


def prime_localization(base: Any, prime: Any) -> tuple[PrimeLocalizationParent, PrimeLocalizationMap]:
    """Construct an Oscar-backed Sage facade for the localization at ``prime``."""

    if not prime.is_prime():
        raise ValueError("prime_localization requires a prime ideal")
    if prime.ring() is not base:
        raise TypeError("prime ideal must belong to the given base ring")

    julia.eval("using Oscar")
    from sage.all import ZZ

    if base is ZZ:
        prime_generators = list(prime.gens())
        if len(prime_generators) != 1:
            raise ValueError("ZZ prime localization requires one principal generator")
        oscar_ring = julia.call("localization", base, prime_generators[0])
        oscar_iota = julia.call("BridgeCoercionMap", base, oscar_ring)
        localization = PrimeLocalizationParent(base, prime, oscar_ring, oscar_iota)
        return localization, PrimeLocalizationMap(base, localization, oscar_iota)

    oscar_prime = julia.call("ideal", base, list(prime.gens()))
    oscar_units = julia.call("complement_of_prime_ideal", oscar_prime, check=False)
    oscar_ring, oscar_iota = julia.call("localization", base, oscar_units)
    localization = PrimeLocalizationParent(base, prime, oscar_ring, oscar_iota)
    return localization, PrimeLocalizationMap(base, localization, oscar_iota)
