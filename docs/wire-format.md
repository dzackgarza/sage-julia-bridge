# Bridge wire format

This document defines the transport, retained-object, and conversion contracts.
`interface.py`, `conversion.py`, `mrdi.py`, and `julia_bridge.jl` implement them.
Display text never establishes identity or conversion.

## Protocol framing

One request occupies one line on the worker's stdin: `op \t base64(payload)`. Protocol version 1 negotiates capabilities with `hello` before ordinary requests.
Operations include `exec`, `value`, `resolve`, `set`, `call`, `call_object`, `object`, `batch`, `materialize`, `release`, `ping`, and `quit`.

A successful reply is `ok \t b64(display) \t b64(structured) \t b64(stdout) \t b64(stderr)`. An error reply is `err \t b64(json_error) \t b64(stdout) \t b64(stderr)`. The JSON error carries `kind`, Julia exception type, message, and backend stack.
Protocol violations, conversion refusal, parent incompatibility, released objects, stale objects, backend dispatch, and worker death remain distinguishable.

## Retained-object runtime

A `handle` node identifies one worker-owned Julia value.
Its Sage proxy records the bridge session and worker generation, so a restarted worker cannot alias an old ID. Repeated references in nested results reuse one backend ID. Each live proxy contributes one retained reference.
`release()` is public and idempotent; garbage collection queues automatic release under the request lock.

Foreign objects support:

- returned and global callables, closures, callable structs, and modules;

- property access and legal mutation;

- indexing, legal indexed mutation, containment, length, and iteration;

- backend equality and identity;

- type, display, applicability, and retained-reference introspection;

- recursive native, foreign, and heterogeneous arguments and results.

`resolve(path)` performs symbol lookup without source evaluation.
`batch` executes a structured operation graph in one request and preserves intermediate identities.
Text evaluation is a separate expert operation.

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
| `handle` | `id`, `julia_type`, `display` | retained worker value | live `JuliaHandle` |
| `mrdi` | `data`: an mrdi document (below) | via `Oscar.Serialization` | via `mrdi.py` |
| `unsupported` | `julia_type` | — (materialize refusal) | raises `TypeError` |

Containers are containers: a Julia `Vector`/`Tuple` decodes to a Python `list`/`tuple`, never a Sage free-module element; a Sage vector iterates into a `vector` node (container semantics; see free modules below).
No parent is ever inferred from container entries — the `int`/`rational` entries of the primitive `matrix` node determine ZZ/QQ *canonically* (ZZ is initial, QQ its prime field), which is the one case where entries fix the parent uniquely.

## mrdi subset

The `mrdi` node is an explicit value-conversion mechanism, not an object-admission gate.
It carries an Oscar serialization document (`_ns`/`_type`/`data`/`_refs`) pinned to `_ns = {"Oscar": [..., "1.7.1"]}`. The Sage decoder rejects other namespaces and versions.
The admissible `_type` names — everywhere in the document, including `_refs` — are exactly:

```
ZZRing  QQField  ZZRingElem  QQFieldElem
Nemo.zzModRing  Nemo.ZZModRing  zzModRingElem  ZZModRingElem
FiniteField  FqFieldElem
PolyRing  PolyRingElem  MPolyRing  MPolyRingElem
MatSpace  MatElem
Vector  Tuple
```

A document containing another `_type` name is outside this conversion subset.
The worker retains the original value as a usable foreign object.
The Sage decoder hard-rejects such a document if received.

MRDI-supported parent objects remain retained by default because copying would erase backend identity.
Explicit `.sage()` conversion may materialize them.
Value-like supported elements continue to convert automatically.

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

## Conversion limits

These values have no built-in native conversion.
Julia results remain usable as retained objects; Sage inputs require a registered outbound conversion:

- `Frac(P)`, quotient rings `A/I`, number fields — tranche 2 (issue #1).

- Free modules and `matrix_ring` (`MatRing`) elements.

- Sage multivariate rings with non-degrevlex term orders (see ordering resolution).

- Floats, balls, p-adics, series, symbolic expressions, embeddings, weighted or block orderings, groups, schemes, morphisms, and other unregistered values.

Conversion refusal raises `JuliaConversionError`. It never releases or invalidates the foreign object.

## Layer boundaries

Dependencies point downward:

1. Native Sage facades and optional domain adapters

2. Sage–Oscar realization maps

3. Explicit conversion registry and MRDI conversion

4. Generic retained-object runtime

5. Versioned subprocess transport

The Python and Julia kernel files do not import domain facades or dispatch on Oscar domain types.
Julia domain adapters live in separate `*_backend.jl` files and load through a generic adapter-discovery rule.
