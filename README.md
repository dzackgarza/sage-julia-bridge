# sage-julia-bridge

`sage-julia-bridge` is a standalone Python package for Sage that talks to a
long-lived Julia subprocess over stdio. It does not patch or modify Sage's
source tree.

The bridge is intentionally small:

- evaluate Julia code as strings
- keep one Julia session alive
- convert a small set of common values between Julia and Sage
- let Sage load Julia packages such as Oscar out of process

Supported structured conversions (both directions):

- integers
- rationals
- vectors
- matrices
- strings
- booleans
- `None` / `nothing`
- Oscar/Nemo integers, rationals, and matrices over ZZ/QQ (converted exactly)

`set(...)` and `call(...)` are protocol operations: values travel as data and
are never interpolated into Julia source. Any result outside the conversions
above comes back from `sage(...)`/`call(...)` as an opaque `JuliaHandle`,
which can be passed back into `set`/`call` and materialized explicitly with
`.sage()` (raising `TypeError` if still unsupported). Inputs outside the
codec (e.g. floats, dicts) are rejected loudly; use `eval(...)` with Julia
source for anything else.

## Install

Bootstrap everything (Python package into Sage, plus Julia dependencies and
artifacts including Oscar):

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
