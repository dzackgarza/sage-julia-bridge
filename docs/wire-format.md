# Bridge wire format

This document is the single source of truth for every value that crosses the bridge in either direction.
Both codecs (`interface.py`/`mrdi.py` on the Sage side, `julia_bridge.jl` on the worker side) implement exactly this grammar.
A value outside it is a `JuliaHandle` (Julia→Sage) or a loud `TypeError` (Sage→Julia input).
Nothing is ever converted by display text, `eval`, or a guessed parent.

## Protocol framing

One request per line on the worker's stdin: `op \t base64(payload)`. Ops: `exec`, `value`, `set`, `call`, `materialize`, `release`, `ping`, `quit`. One reply per line: `ok \t b64(display) \t b64(structured) \t b64(stdout) \t b64(stderr)` or `err \t b64(message) \t b64("") \t b64("")`.

## Bridge nodes

The `structured` slot and all `set`/`call` values are JSON trees of these nodes and no others:

| node | fields | Julia value | Sage/Python value |
| --- | --- | --- | --- |
| `nothing` | — | `nothing` | `None` |
| `bool` | `value` | `Bool` | `bool` |
| `string` | `value` | `String` | `str` |
| `int` | `value`: decimal string | `Int` if it fits, else `BigInt` | Sage `ZZ` |
| `rational` | `num`, `den`: decimal strings | `Rational{BigInt}` | Sage `QQ` |
| `vector` | `data`: list of nodes | `Vector` (container) | Python `list` |
| `matrix` | `nrows`, `ncols`, `data`: row-major nodes | `Matrix` | Sage matrix (ring determined by the typed entries) |
| `handle` | `id`, `julia_type`, `display` | worker `HANDLES[id]` | `JuliaHandle` |
| `mrdi` | `data`: an mrdi document (below) | via `Oscar.Serialization` | via `mrdi.py` |
| `unsupported` | `julia_type` | — (materialize refusal) | raises `TypeError` |

Containers are containers: a Julia `Vector`/`Tuple` decodes to a Python `list`/`tuple`, never a Sage free-module element; a Sage vector iterates into a `vector` node (container semantics; see free modules below).
No parent is ever inferred from container entries — the `int`/`rational` entries of the primitive `matrix` node determine ZZ/QQ *canonically* (ZZ is initial, QQ its prime field), which is the one case where entries fix the parent uniquely.

## mrdi subset

The `mrdi` node carries a document in Oscar's serialization format (`_ns`/`_type`/`data`/`_refs`), pinned to `_ns = {"Oscar": [..., "1.7.1"]}`. The Sage decoder rejects any other namespace or version.
The admissible `_type` names — everywhere in the document, including `_refs` — are exactly:

```
ZZRing  QQField  ZZRingElem  QQFieldElem
Nemo.zzModRing  Nemo.ZZModRing  zzModRingElem  ZZModRingElem
FiniteField  FqFieldElem
PolyRing  PolyRingElem  MPolyRing  MPolyRingElem
MatSpace  MatElem
Vector  Tuple
```

A document containing any other `_type` name is outside the subset: the worker routes the value to a handle instead of emitting it, and the Sage decoder hard-rejects it if received.

### Parent identifications

For each supported parent presentation, decoding constructs the counterpart through the target system's canonical constructor, so parent identity is discharged by Sage's `UniqueRepresentation`/factory caching and Nemo's constructor caching:

| mrdi parent | data | Sage parent |
| --- | --- | --- |
| `ZZRing` | — | `ZZ` |
| `QQField` | — | `QQ` |
| `Nemo.zzModRing` / `Nemo.ZZModRing` | modulus string | `IntegerModRing(n)` |
| `FiniteField` (prime) | `"p"` | `GF(p)` |
| `FiniteField` (extension) | sparse defining polynomial over the `PolyRing` in `params` | `GF(p^n, name, modulus=...)` with the explicit modulus; the generator maps to the class of `x` mod that polynomial |
| `PolyRing` | `{"symbols": [s]}`, base in `params` | `PolynomialRing(base, s)` |
| `MPolyRing` | `{"symbols": [...]}`, base in `params` | `PolynomialRing(base, names, order="degrevlex")` |
| `MatSpace` | `{"nrows", "ncols"}`, base in `params` | `MatrixSpace(base, nrows, ncols)` |

**Ordering resolution (pinned).** Oscar rings carry no mathematical monomial ordering (orderings are per-computation arguments; `internal_ordering` is storage detail and is not serialized).
Ring identity therefore excludes the ordering.
Sage multivariate rings are always reconstructed with `degrevlex`; a Sage ring with any other term order is rejected loudly on input.
This supersedes the ordering vocabulary in issue #1's discussion, which assumed Oscar rings carry orderings.

### Element data shapes

| mrdi element | data |
| --- | --- |
| `ZZRingElem` | decimal string |
| `QQFieldElem` | `"a//b"` or `"a"` |
| `zzModRingElem` / `ZZModRingElem` | least nonnegative residue, decimal string |
| `FqFieldElem` (prime field) | decimal string |
| `FqFieldElem` (extension) | sparse power-basis pairs `[["i", c], ...]` |
| `PolyRingElem` | sparse pairs `[["i", coeff], ...]`, coeffs in the base encoding |
| `MPolyRingElem` | sparse terms `[[["e1",...,"er"], coeff], ...]` |
| `MatElem` | row-major list of rows of base-encoded entries |
| `Vector` | list of element data, eltype in `_type.params` |
| `Tuple` | list of element data, componentwise types in `_type.params` |

Residues are normalized to `[0, n)`. Rationals are validated by the target constructors (zero denominators and non-normalized fractions are their errors to raise).

### Validation layering (pinned)

Schema-layer violations are hard protocol rejections by the decoder itself: unknown `_ns`/version, `_type` names outside the whitelist, dangling `_refs`, malformed dimensions or exponent-vector lengths.
Mathematics-layer violations (zero denominator, reducible claimed-irreducible modulus, residue out of range) are delegated to the target parent constructors and their errors propagate unmodified.
Neither layer ever falls back to display text.

## Explicit non-goals of this subset

Routed to handles (Julia→Sage) or rejected loudly (Sage→Julia input):

- `Frac(P)`, quotient rings `A/I`, number fields — tranche 2 (issue #1).

- Free modules and `matrix_ring` (`MatRing`) elements — Oscar 1.7.1 cannot serialize them (`save` raises); revisit when upstream can.

- Sage multivariate rings with non-degrevlex term orders (see ordering resolution).

- Floats, balls, p-adics, series, symbolic expressions, embeddings, weighted or block orderings, groups, schemes, morphisms, and everything else not listed above.
