using Base64

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

function encode_value(x)
    encoded = encode_supported(x)
    if encoded === nothing
        return "{\"type\":\"unsupported\",\"julia_type\":" * json_string(string(typeof(x))) * "}"
    end
    return encoded
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

function evaluate(code::AbstractString)
    stdout_pipe = Pipe()
    stderr_pipe = Pipe()
    value = nothing
    redirect_stdio(stdout=stdout_pipe, stderr=stderr_pipe) do
        try
            value = Base.include_string(Main, code, "sage_julia_bridge")
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

function reply(parts::Vector{String})
    println(stdout, join(parts, '\t'))
    flush(stdout)
end

for line in eachline(stdin)
    isempty(line) && continue
    pieces = split(line, '\t'; limit=2)
    op = pieces[1]
    payload = length(pieces) == 2 ? String(base64decode(pieces[2])) : ""
    if op == "quit"
        reply(["ok", b64(""), b64("{\"type\":\"nothing\"}"), b64(""), b64("")])
        break
    elseif op == "ping"
        reply(["ok", b64("pong"), b64("{\"type\":\"string\",\"value\":\"pong\"}"), b64(""), b64("")])
        continue
    elseif op != "exec"
        reply(["err", b64("unknown bridge operation: " * op), b64(""), b64("")])
        continue
    end

    try
        value, stdout_text, stderr_text = evaluate(payload)
        # invokelatest: the loop body runs in the world age of script load,
        # so methods and global bindings introduced by evaluated code (e.g.
        # `using Oscar`) are invisible to direct calls from here.
        reply([
            "ok",
            b64(Base.invokelatest(display_text, value)),
            b64(Base.invokelatest(encode_value, value)),
            b64(stdout_text),
            b64(stderr_text),
        ])
    catch ex
        message = sprint(io -> showerror(io, ex, catch_backtrace()))
        reply(["err", b64(message), b64(""), b64("")])
    end
end
