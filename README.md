# sage-julia-bridge

`sage-julia-bridge` is a standalone Python package for Sage that talks to a
long-lived Julia subprocess over stdio. It does not patch or modify Sage's
source tree.

The bridge is intentionally small:

- evaluate Julia code as strings
- keep one Julia session alive
- convert a small set of common values between Julia and Sage
- let Sage load Julia packages such as Oscar out of process

Supported structured conversions:

- integers
- rationals
- vectors
- matrices
- strings
- booleans

Everything else is still evaluable, but `sage(...)` will refuse to coerce the
result and `eval(...)` will return Julia's textual output.

## Install

Install the package into Sage's Python environment:

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
just install
just test
just lint
just fmt
just build
```
