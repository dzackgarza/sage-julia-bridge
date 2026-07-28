# sage-julia-bridge

`sage-julia-bridge` connects Sage to a retained Julia/Oscar object session over a versioned subprocess protocol.
It installs as a standalone Sage package and does not patch Sage.

Every Julia result remains usable, including package-defined types unknown to the bridge.
`JuliaHandle` is a live foreign object with backend identity, calls, properties, mutation, indexing, containment, iteration, equality, introspection, and deterministic release.
Nested results preserve repeated references.
Structured batches compose several backend operations in one subprocess request.

Native conversion is separate from foreign-object use.
Value-like integers, rationals, finite-field elements, polynomials, vectors, and matrices convert automatically when the pinned MRDI conversion supports them.
Identity-bearing parents and maps remain retained by default.
Call `.sage()` for explicit conversion; typed refusal leaves the foreign object usable.
Conversion registries extend both directions without changing the object runtime.

The first native Sage facade is prime localization:

```python
from sage.all import GF, QQ, PolynomialRing
from sage_julia_bridge import prime_localization

R = PolynomialRing(QQ, ("x", "y"), order="degrevlex")
x, y = R.gens()
L, iota = prime_localization(R, R.ideal(x))
m = L.maximal_ideal()
k, rho = L.residue_field()

assert not iota(x).is_unit()
assert iota(y).is_unit()
assert rho(iota(x)) == k.zero()
assert iota(x) in m
```

`L`, its elements and ideals, `iota`, `k`, and `rho` are native Sage parents, elements, ideals, fields, and ring morphisms.
Each facade exposes its retained backend realization through `.oscar()`.

## Install

Bootstrap everything (Python package into Sage, plus Julia dependencies and artifacts including Oscar):

```bash
just setup
```

Or install just the Python package into Sage's environment:

```bash
sage -python -m pip install -e .
```

## Use

```python
from sage_julia_bridge import julia

print(julia.eval("VERSION"))
print(julia.sage("1 // 2"))

julia.eval("using Oscar")
print(julia.eval("""begin
R, (x, y) = QQ[:x, :y]
x^2 + y^2
end"""))

julia.quit()
```

Obtain global modules, functions, and constructors without evaluating source:

```python
julia.eval("using Oscar")
oscar = julia.resolve("Oscar")
matrix_algebra = oscar.getproperty("matrix_algebra")
A = matrix_algebra(QQ, 2)
assert julia.call("dimension", A) == 4
```

`eval(...)` remains an explicit expert escape hatch.
Normal object composition, conversion, and localization do not interpolate values into Julia source.

See [the wire and object protocol](docs/wire-format.md) for framing, conversion policy, lifecycle, batching, and error contracts.

You can also create isolated sessions:

```python
from sage_julia_bridge import Julia

bridge = Julia()
bridge.set("v", vector(QQ, [1, QQ(2) / 3, 3]))
print(bridge.get_sage("v"))
bridge.quit()
```

## Development

All project commands go through `just`:

```bash
just setup
just test
just build
```
