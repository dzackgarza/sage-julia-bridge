using Base64
import JSON

function json_escape(s::AbstractString)
    io = IOBuffer()
    for c in s
        if c == '"'
            print(io, "\\\"")
        elseif c == '\\'
            print(io, "\\\\")
        elseif c == '\b'
            print(io, "\\b")
        elseif c == '\f'
            print(io, "\\f")
        elseif c == '\n'
            print(io, "\\n")
        elseif c == '\r'
            print(io, "\\r")
        elseif c == '\t'
            print(io, "\\t")
        elseif Int(c) < 0x20
            print(io, "\\u", lowercase(string(Int(c), base=16, pad=4)))
        else
            print(io, c)
        end
    end
    return String(take!(io))
end

json_string(s::AbstractString) = "\"" * json_escape(s) * "\""

# Canonical exact conversions for Nemo values (issue #1, M1). ZZ is initial in
# commutative rings and QQ is its prime field, so these four parents admit a
# unique identification with their Base models; the conversion is lossless and
# the existing Integer/Rational/AbstractMatrix branches handle the rest.
# Resolved at encode time because the worker starts before Oscar is loaded;
# Nemo symbols must not be referenced at parse time. Nemo is recovered from
# the value's own type so qualified loads (`import Oscar`) work without a
# Main.Nemo binding.
function nemo_to_base(x)
    Nemo = parentmodule(typeof(x))
    nameof(Nemo) === :Nemo || return nothing
    x isa Nemo.ZZRingElem && return BigInt(x)
    x isa Nemo.QQFieldElem && return Rational{BigInt}(x)
    x isa Nemo.ZZMatrix && return Matrix{BigInt}(x)
    x isa Nemo.QQMatrix && return Matrix{Rational{BigInt}}(x)
    return nothing
end

function encode_supported(x)
    converted = nemo_to_base(x)
    if converted !== nothing
        return encode_supported(converted)
    end
    if x === nothing
        return "{\"type\":\"nothing\"}"
    elseif x isa Bool
        return "{\"type\":\"bool\",\"value\":" * (x ? "true" : "false") * "}"
    elseif x isa AbstractString
        return "{\"type\":\"string\",\"value\":" * json_string(x) * "}"
    elseif x isa Integer
        return "{\"type\":\"int\",\"value\":" * json_string(string(x)) * "}"
    elseif x isa Rational
        return (
            "{\"type\":\"rational\",\"num\":" * json_string(string(numerator(x))) *
            ",\"den\":" * json_string(string(denominator(x))) * "}"
        )
    elseif x isa AbstractVector
        values = String[]
        for item in x
            encoded = encode_supported(item)
            encoded === nothing && return nothing
            push!(values, encoded)
        end
        return "{\"type\":\"vector\",\"data\":[" * join(values, ",") * "]}"
    elseif x isa AbstractMatrix
        values = String[]
        for i in axes(x, 1), j in axes(x, 2)
            encoded = encode_supported(x[i, j])
            encoded === nothing && return nothing
            push!(values, encoded)
        end
        return (
            "{\"type\":\"matrix\",\"nrows\":" * string(size(x, 1)) *
            ",\"ncols\":" * string(size(x, 2)) *
            ",\"data\":[" * join(values, ",") * "]}"
        )
    end
    return nothing
end

# Opaque references to worker-held values the structured codec does not
# cover. Keyed by a monotone id; entries live until the client releases them.
const HANDLES = Dict{Int,Any}()
const HANDLE_COUNTER = Ref(0)

function register_handle(x)
    id = (HANDLE_COUNTER[] += 1)
    HANDLES[id] = x
    return id
end

# wrap=true: uncovered values become handles (sage()/call() results).
# wrap=false: uncovered values report unsupported (explicit materialization).
function encode_value(x, wrap::Bool)
    encoded = encode_supported(x)
    encoded === nothing || return encoded
    if !wrap
        return "{\"type\":\"unsupported\",\"julia_type\":" * json_string(string(typeof(x))) * "}"
    end
    id = register_handle(x)
    return (
        "{\"type\":\"handle\",\"id\":" * string(id) *
        ",\"julia_type\":" * json_string(string(typeof(x))) *
        ",\"display\":" * json_string(display_text(x)) * "}"
    )
end

function decode_value(node::AbstractDict)
    kind = node["type"]::String
    if kind == "nothing"
        return nothing
    elseif kind == "bool"
        return node["value"]::Bool
    elseif kind == "string"
        return String(node["value"]::String)
    elseif kind == "int"
        big = parse(BigInt, node["value"]::String)
        return typemin(Int) <= big <= typemax(Int) ? Int(big) : big
    elseif kind == "rational"
        num = parse(BigInt, node["num"]::String)
        den = parse(BigInt, node["den"]::String)
        return Rational{BigInt}(num, den)
    elseif kind == "vector"
        return [decode_value(item) for item in node["data"]]
    elseif kind == "matrix"
        nrows = node["nrows"]::Int
        ncols = node["ncols"]::Int
        data = node["data"]
        length(data) == nrows * ncols ||
            error("matrix payload has ", length(data), " entries for ", nrows, "x", ncols)
        return [decode_value(data[(i - 1) * ncols + j]) for i in 1:nrows, j in 1:ncols]
    elseif kind == "handle"
        id = node["id"]::Int
        haskey(HANDLES, id) || error("unknown handle id: ", id)
        return HANDLES[id]
    end
    error("unknown bridge value type: ", kind)
end

# Values are never interpolated into source: a function path is resolved as a
# chain of symbol lookups, which cannot execute code.
function resolve_path(path::AbstractString)
    parts = split(path, '.')
    all(!isempty, parts) || error("invalid function path: ", path)
    obj = Main
    for part in parts
        obj = getproperty(obj, Symbol(part))
    end
    return obj
end

function b64(s::AbstractString)
    return base64encode(Vector{UInt8}(codeunits(s)))
end

function display_text(value)
    value === nothing && return ""
    try
        return string(value)
    catch
        return sprint(show, value)
    end
end

# The protocol runs over this process's stdout, so anything user code prints
# must be captured or it corrupts the framing.
function capture(f)
    stdout_pipe = Pipe()
    stderr_pipe = Pipe()
    value = nothing
    redirect_stdio(stdout=stdout_pipe, stderr=stderr_pipe) do
        try
            value = f()
        finally
            close(stdout_pipe.in)
            close(stderr_pipe.in)
        end
    end
    return (
        value,
        read(stdout_pipe, String),
        read(stderr_pipe, String),
    )
end

function evaluate(code::AbstractString)
    return capture(() -> Base.include_string(Main, code, "sage_julia_bridge"))
end

function reply(parts::Vector{String})
    println(stdout, join(parts, '\t'))
    flush(stdout)
end

const NOTHING_NODE = "{\"type\":\"nothing\"}"

# Returns (display, structured, stdout, stderr) for the ok reply.
function handle_request(op::String, payload::String)
    if op == "exec"
        value, stdout_text, stderr_text = evaluate(payload)
        return (display_text(value), NOTHING_NODE, stdout_text, stderr_text)
    elseif op == "value"
        value, stdout_text, stderr_text = evaluate(payload)
        return (display_text(value), encode_value(value, true), stdout_text, stderr_text)
    elseif op == "set"
        request = JSON.parse(payload)
        name = Symbol(request["name"]::String)
        value = decode_value(request["value"])
        # One eval creating binding and assignment together: a two-step
        # declare-then-setglobal! fails because the new binding is not
        # visible in this function's world age. QuoteNode keeps the value
        # verbatim data; it is never parsed or evaluated as code.
        Core.eval(Main, Expr(:(=), name, QuoteNode(value)))
        return ("", NOTHING_NODE, "", "")
    elseif op == "call"
        request = JSON.parse(payload)
        f = resolve_path(request["function"]::String)
        args = Any[decode_value(item) for item in request["args"]]
        kwargs = Pair{Symbol,Any}[Symbol(key) => decode_value(item) for (key, item) in request["kwargs"]]
        value, stdout_text, stderr_text = capture(() -> f(args...; kwargs...))
        return (display_text(value), encode_value(value, true), stdout_text, stderr_text)
    elseif op == "materialize"
        id = parse(Int, payload)
        haskey(HANDLES, id) || error("unknown handle id: ", id)
        x = HANDLES[id]
        return (display_text(x), encode_value(x, false), "", "")
    elseif op == "release"
        id = parse(Int, payload)
        haskey(HANDLES, id) || error("unknown handle id: ", id)
        delete!(HANDLES, id)
        return ("", NOTHING_NODE, "", "")
    end
    error("unknown bridge operation: ", op)
end

for line in eachline(stdin)
    isempty(line) && continue
    pieces = split(line, '\t'; limit=2)
    op = String(pieces[1])
    payload = length(pieces) == 2 ? String(base64decode(pieces[2])) : ""
    if op == "quit"
        reply(["ok", b64(""), b64(NOTHING_NODE), b64(""), b64("")])
        break
    elseif op == "ping"
        reply(["ok", b64("pong"), b64("{\"type\":\"string\",\"value\":\"pong\"}"), b64(""), b64("")])
        continue
    end

    try
        # invokelatest: the loop body runs in the world age of script load,
        # so methods and global bindings introduced by evaluated code (e.g.
        # `using Oscar`) are invisible to direct calls from here.
        display, structured, stdout_text, stderr_text = Base.invokelatest(handle_request, op, payload)
        reply([
            "ok",
            b64(display),
            b64(structured),
            b64(stdout_text),
            b64(stderr_text),
        ])
    catch ex
        message = sprint(io -> showerror(io, ex, catch_backtrace()))
        reply(["err", b64(message), b64(""), b64("")])
    end
end
