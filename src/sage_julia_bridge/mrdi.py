"""Sage-side codec for the pinned mrdi subset.

Implements exactly the grammar in docs/wire-format.md. decode_mrdi turns an
mrdi document into a Sage object, hard-rejecting schema violations and
propagating mathematics-layer errors from Sage's constructors. encode_mrdi
turns a supported Sage value into an mrdi document, returning None for values
outside the subset (the caller owns the loud rejection).
"""

from __future__ import annotations

import json
import uuid

from sage.matrix.matrix_space import MatrixSpace
from sage.rings.finite_rings.element_base import FinitePolyExtElement
from sage.rings.finite_rings.finite_field_base import FiniteField
from sage.rings.finite_rings.finite_field_constructor import GF
from sage.rings.finite_rings.integer_mod import IntegerMod_abstract
from sage.rings.finite_rings.integer_mod_ring import (
    IntegerModRing,
    IntegerModRing_generic,
)
from sage.rings.integer_ring import ZZ
from sage.rings.polynomial.multi_polynomial import MPolynomial
from sage.rings.polynomial.multi_polynomial_ring_base import MPolynomialRing_base
from sage.rings.polynomial.polynomial_element import Polynomial
from sage.rings.polynomial.polynomial_ring import PolynomialRing_generic
from sage.rings.polynomial.polynomial_ring_constructor import PolynomialRing
from sage.rings.rational_field import QQ
from sage.structure.element import Matrix

from sage_julia_bridge.errors import JuliaProtocolError

OSCAR_NS_URL = "https://github.com/oscar-system/Oscar.jl"
OSCAR_NS_VERSION = "1.7.1"

# The wire format does not carry generator names for extension fields; the
# reconstruction name is pinned here (docs/wire-format.md).
EXTENSION_GENERATOR_NAME = "a"

# Moduli up to a machine word use Nemo's word-size residue ring, matching
# Oscar's residue_ring(ZZ, ::Int) behavior.
MACHINE_WORD_MAX = 2**63 - 1

PARENT_TYPES = frozenset(
    {
        "ZZRing",
        "QQField",
        "Nemo.zzModRing",
        "Nemo.ZZModRing",
        "FiniteField",
        "PolyRing",
        "MPolyRing",
        "MatSpace",
    }
)
ELEMENT_TYPES = frozenset(
    {
        "ZZRingElem",
        "QQFieldElem",
        "zzModRingElem",
        "ZZModRingElem",
        "FqFieldElem",
        "PolyRingElem",
        "MPolyRingElem",
        "MatElem",
        "Vector",
        "Tuple",
    }
)
WHITELIST = PARENT_TYPES | ELEMENT_TYPES


def _type_name(type_desc: object) -> str:
    if isinstance(type_desc, str):
        return type_desc
    if isinstance(type_desc, dict):
        name = type_desc.get("name")
        if isinstance(name, str):
            return name
    raise JuliaProtocolError(f"malformed mrdi type descriptor: {type_desc!r}")


def _walk_type_names(node: object, in_type: bool, names: set[str]) -> None:
    if isinstance(node, dict):
        if in_type and isinstance(node.get("name"), str):
            names.add(node["name"])
        for key, value in node.items():
            if key == "_type":
                if isinstance(value, str):
                    names.add(value)
                else:
                    _walk_type_names(value, True, names)
            elif key == "params" and in_type:
                _walk_type_names(value, True, names)
            else:
                _walk_type_names(value, False, names)
    elif isinstance(node, list):
        for item in node:
            _walk_type_names(item, in_type, names)


def validate_document(doc: dict) -> None:
    """Schema-layer validation: namespace, version, and type whitelist."""
    ns = doc.get("_ns")
    if not isinstance(ns, dict) or "Oscar" not in ns:
        raise JuliaProtocolError(f"mrdi document lacks an Oscar namespace: {ns!r}")
    version = ns["Oscar"][1] if len(ns["Oscar"]) > 1 else None
    if version != OSCAR_NS_VERSION:
        raise JuliaProtocolError(
            f"unsupported mrdi schema version {version!r}; "
            f"this bridge is pinned to Oscar {OSCAR_NS_VERSION}"
        )
    names: set[str] = set()
    _walk_type_names(doc, False, names)
    outside = names - WHITELIST
    if outside:
        raise JuliaProtocolError(
            f"mrdi document contains types outside the supported subset: "
            f"{sorted(outside)}"
        )


def _resolve(spec: object, refs: dict) -> dict:
    """Resolve a params entry to an inline spec dict, following ref UUIDs."""
    if isinstance(spec, str):
        if spec not in refs:
            raise JuliaProtocolError(f"dangling mrdi reference: {spec!r}")
        return refs[spec]
    if isinstance(spec, dict):
        return spec
    raise JuliaProtocolError(f"malformed mrdi params: {spec!r}")


def _decode_parent(spec: dict, refs: dict) -> object:
    name = _type_name(spec["_type"])
    if name == "ZZRing":
        return ZZ
    if name == "QQField":
        return QQ
    if name in ("Nemo.zzModRing", "Nemo.ZZModRing"):
        return IntegerModRing(ZZ(spec["data"]))
    if name == "FiniteField":
        data = spec["data"]
        if isinstance(data, str):
            return GF(ZZ(data))
        poly_ring_spec = _resolve(spec["_type"]["params"], refs)
        modulus_ring = _decode_parent(poly_ring_spec, refs)
        modulus = _element_from_data(modulus_ring, data, refs)
        p = modulus_ring.base_ring().characteristic()
        return GF(
            p ** modulus.degree(), EXTENSION_GENERATOR_NAME, modulus=modulus
        )
    if name == "PolyRing":
        base = _decode_parent(_resolve(spec["_type"]["params"], refs), refs)
        symbols = spec["data"]["symbols"]
        if len(symbols) != 1:
            raise JuliaProtocolError(
                f"PolyRing must have exactly one symbol: {symbols!r}"
            )
        return PolynomialRing(base, symbols[0])
    if name == "MPolyRing":
        base = _decode_parent(_resolve(spec["_type"]["params"], refs), refs)
        symbols = spec["data"]["symbols"]
        return PolynomialRing(base, symbols, order="degrevlex")
    if name == "MatSpace":
        base = _decode_parent(_resolve(spec["_type"]["params"], refs), refs)
        return MatrixSpace(
            base, ZZ(spec["data"]["nrows"]), ZZ(spec["data"]["ncols"])
        )
    raise JuliaProtocolError(f"unsupported mrdi parent type: {name!r}")


def _rational_from_string(raw: str) -> object:
    if "//" in raw:
        num, den = raw.split("//")
        return QQ(ZZ(num)) / QQ(ZZ(den))
    return QQ(ZZ(raw))


def _element_from_data(parent: object, raw: object, refs: dict) -> object:
    if parent is ZZ:
        return ZZ(raw)
    if parent is QQ:
        return _rational_from_string(raw)
    if isinstance(parent, FiniteField):
        if isinstance(raw, str):
            return parent(ZZ(raw))
        gen = parent.gen()
        result = parent(0)
        for exp, coeff in raw:
            result += parent(ZZ(coeff)) * gen ** int(exp)
        return result
    if isinstance(parent, IntegerModRing_generic):
        return parent(ZZ(raw))
    if isinstance(parent, MatrixSpace):
        base = parent.base_ring()
        rows = [
            [_element_from_data(base, entry, refs) for entry in row]
            for row in raw
        ]
        if len(rows) != parent.nrows() or any(
            len(row) != parent.ncols() for row in rows
        ):
            raise JuliaProtocolError(
                f"matrix payload shape does not match "
                f"{parent.nrows()}x{parent.ncols()}"
            )
        return parent(rows)
    if isinstance(parent, (PolynomialRing_generic, MPolynomialRing_base)):
        # Term shapes: PolyRingElem pairs ["i", c]; MPolyRingElem pairs
        # [["e1",...], c]. A 1-symbol MPolyRing decodes to a univariate
        # Sage ring, so keys follow the parent's arity, not the shape.
        base = parent.base_ring()
        univariate = parent.ngens() == 1
        coeffs = {}
        for exps, coeff in raw:
            exponents = [int(e) for e in exps] if isinstance(exps, list) else [
                int(exps)
            ]
            if len(exponents) != parent.ngens():
                raise JuliaProtocolError(
                    f"exponent vector {exponents} does not match "
                    f"{parent.ngens()} generators"
                )
            key = exponents[0] if univariate else tuple(exponents)
            coeffs[key] = _element_from_data(base, coeff, refs)
        return parent(coeffs)
    raise JuliaProtocolError(f"cannot decode element for parent {parent!r}")


def decode_mrdi(doc: dict) -> object:
    validate_document(doc)
    refs = doc.get("_refs", {})
    type_desc = doc["_type"]
    name = _type_name(type_desc)
    # Parent docs for ZZ/QQ carry no data at all.
    data = doc.get("data")
    if name in PARENT_TYPES:
        return _decode_parent({"_type": type_desc, "data": data}, refs)
    if name == "ZZRingElem":
        return ZZ(data)
    if name == "QQFieldElem":
        return _rational_from_string(data)
    if name == "Vector":
        element_type = type_desc["params"]
        return [
            decode_mrdi(
                {
                    "_ns": doc["_ns"],
                    "_type": element_type,
                    "data": item,
                    "_refs": refs,
                }
            )
            for item in data
        ]
    if name == "Tuple":
        component_types = type_desc["params"]
        if len(component_types) != len(data):
            raise JuliaProtocolError("tuple arity does not match its params")
        return tuple(
            decode_mrdi(
                {
                    "_ns": doc["_ns"],
                    "_type": component_type,
                    "data": item,
                    "_refs": refs,
                }
            )
            for component_type, item in zip(component_types, data)
        )
    if name in ELEMENT_TYPES:
        parent = _decode_parent(_resolve(type_desc["params"], refs), refs)
        return _element_from_data(parent, data, refs)
    raise JuliaProtocolError(f"unsupported mrdi type: {name!r}")


# -- Encoding: Sage value -> mrdi document ----------------------------------


# Oscar's deserializer keeps a global registry keyed by ref UUID: documents
# whose refs share a UUID decode to the SAME parent object. Deriving the
# UUID from the canonical spec content makes every encoding of one parent
# presentation hit that registry, which is what discharges the shared-parent
# requirement across separate payloads.
_REF_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, OSCAR_NS_URL)


def _new_ref(refs: dict, spec: dict) -> str:
    key = str(uuid.uuid5(_REF_NAMESPACE, json.dumps(spec, sort_keys=True)))
    refs[key] = spec
    return key


def _encode_parent(parent: object, refs: dict) -> object | None:
    """Return the params entry for a parent (inline dict or ref UUID)."""
    if parent is ZZ:
        return {"_type": "ZZRing"}
    if parent is QQ:
        return {"_type": "QQField"}
    if isinstance(parent, FiniteField):
        if parent.degree() == 1:
            return _new_ref(
                refs,
                {
                    "_type": {"name": "FiniteField", "_instance": "FqField"},
                    "data": str(parent.characteristic()),
                },
            )
        prime_ref = _encode_parent(GF(parent.characteristic()), refs)
        poly_ring_ref = _new_ref(
            refs,
            {
                "_type": {"name": "PolyRing", "params": prime_ref},
                "data": {"symbols": ["x"]},
            },
        )
        modulus = parent.modulus()
        return _new_ref(
            refs,
            {
                "_type": {
                    "name": "FiniteField",
                    "_instance": "FqField",
                    "params": poly_ring_ref,
                },
                "data": [
                    [str(exp), str(int(coeff))]
                    for exp, coeff in sorted(modulus.dict().items())
                ],
            },
        )
    if isinstance(parent, IntegerModRing_generic):
        modulus = parent.order()
        ring_type = (
            "Nemo.zzModRing" if modulus <= MACHINE_WORD_MAX else "Nemo.ZZModRing"
        )
        return {"_type": ring_type, "data": str(modulus)}
    if isinstance(parent, MatrixSpace):
        base_params = _encode_parent(parent.base_ring(), refs)
        if base_params is None:
            return None
        return _new_ref(
            refs,
            {
                "_type": {"name": "MatSpace", "params": base_params},
                "data": {
                    "nrows": str(parent.nrows()),
                    "ncols": str(parent.ncols()),
                },
            },
        )
    if isinstance(parent, MPolynomialRing_base):
        if parent.term_order().name() != "degrevlex":
            return None
        base_params = _encode_parent(parent.base_ring(), refs)
        if base_params is None:
            return None
        return _new_ref(
            refs,
            {
                "_type": {"name": "MPolyRing", "params": base_params},
                "data": {"symbols": [str(g) for g in parent.gens()]},
            },
        )
    if isinstance(parent, PolynomialRing_generic):
        base_params = _encode_parent(parent.base_ring(), refs)
        if base_params is None:
            return None
        return _new_ref(
            refs,
            {
                "_type": {"name": "PolyRing", "params": base_params},
                "data": {"symbols": [str(parent.gen())]},
            },
        )
    return None


def _element_data(value: object) -> object:
    parent = value.parent()
    if parent is ZZ:
        return str(value)
    if parent is QQ:
        den = value.denominator()
        return str(value) if den == 1 else f"{value.numerator()}//{den}"
    if isinstance(value, IntegerMod_abstract):
        return str(int(value))
    if isinstance(value, FinitePolyExtElement):
        return [
            [str(exp), str(int(coeff))]
            for exp, coeff in sorted(value.polynomial().dict().items())
        ]
    if isinstance(value, Polynomial):
        return [
            [str(exp), _element_data(coeff)]
            for exp, coeff in sorted(value.dict().items())
        ]
    if isinstance(value, MPolynomial):
        return [
            [[str(e) for e in exps], _element_data(coeff)]
            for exps, coeff in sorted(value.dict().items())
        ]
    if isinstance(value, Matrix):
        return [
            [_element_data(value[i, j]) for j in range(value.ncols())]
            for i in range(value.nrows())
        ]
    raise JuliaProtocolError(f"cannot encode element data for {value!r}")


_ELEMENT_TYPE_BY_KIND = {
    "gf": "FqFieldElem",
    "zmod_small": "zzModRingElem",
    "zmod_big": "ZZModRingElem",
    "poly": "PolyRingElem",
    "mpoly": "MPolyRingElem",
    "matrix": "MatElem",
}


def _element_kind(value: object) -> str | None:
    if isinstance(value, FinitePolyExtElement):
        return "gf"
    if isinstance(value, IntegerMod_abstract):
        if value.parent().is_field():
            return "gf"
        modulus = value.parent().order()
        return "zmod_small" if modulus <= MACHINE_WORD_MAX else "zmod_big"
    if isinstance(value, MPolynomial):
        return "mpoly"
    if isinstance(value, Polynomial):
        return "poly"
    if isinstance(value, Matrix):
        # ZZ/QQ matrices take the canonical primitive node (M1); mrdi
        # carries matrices over every other supported base.
        if value.base_ring() in (ZZ, QQ):
            return None
        return "matrix"
    return None


_PARENT_CLASSES = (
    FiniteField,
    IntegerModRing_generic,
    MPolynomialRing_base,
    PolynomialRing_generic,
    MatrixSpace,
)


def encode_mrdi(value: object) -> dict | None:
    """Encode a supported Sage value; None means outside the subset."""
    if value is ZZ or value is QQ or isinstance(value, _PARENT_CLASSES):
        refs: dict = {}
        params = _encode_parent(value, refs)
        if params is None:
            return None
        # Lift the parent's own spec to the top level of the document,
        # keeping its deterministic UUID as the top-level "id": Oscar seeds
        # its deserializer registry from that id, so elements sent in later
        # payloads (whose _refs carry the same UUID) share this parent.
        doc = {"_ns": {"Oscar": [OSCAR_NS_URL, OSCAR_NS_VERSION]}}
        if isinstance(params, str):
            spec = refs.pop(params)
            doc["id"] = params
        else:
            spec = params
        doc["_type"] = spec["_type"]
        if "data" in spec:
            doc["data"] = spec["data"]
        if refs:
            doc["_refs"] = refs
        return doc
    kind = _element_kind(value)
    if kind is None:
        return None
    refs = {}
    params = _encode_parent(value.parent(), refs)
    if params is None:
        return None
    doc = {
        "_ns": {"Oscar": [OSCAR_NS_URL, OSCAR_NS_VERSION]},
        "_type": {"name": _ELEMENT_TYPE_BY_KIND[kind], "params": params},
        "data": _element_data(value),
    }
    if refs:
        doc["_refs"] = refs
    return doc
