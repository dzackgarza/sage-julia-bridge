# Report where a fresh depot can fetch each artifact that `Pkg.instantiate()`
# downloads for the worker project: the package server, a declared upstream
# URL, or nowhere. Pkg tries the package server first and then the declared
# URLs in order (Pkg.Artifacts.download_artifact); this script follows the
# same selection, `select_downloadable_artifacts` for the host platform with
# lazy artifacts excluded, and probes each source with an HTTP HEAD request.
#
# files.gap-system.org does not count as a source: it timed out on the image
# runner when it was the only one serving GAP_pkg_crisp (issue #17).
using Pkg, Downloads, JSON
using Pkg.Artifacts: select_downloadable_artifacts
using Base.BinaryPlatforms: HostPlatform

const UNRELIABLE_HOST = "files.gap-system.org"

# With throw=false a host that refuses or times out yields a RequestError,
# which is a source that does not serve the artifact.
function serves(url)
    response = Downloads.request(url; method="HEAD", throw=false)
    return response isa Downloads.Response && response.status == 200
end

function source(server, meta)
    serves("$server/artifact/$(meta["git-tree-sha1"])") && return "package server"
    for download in meta["download"]
        url = download["url"]
        occursin(UNRELIABLE_HOST, url) && continue
        serves(url) && return url
    end
    return nothing
end

artifacts = Pair{String,Dict{String,Any}}[]
for info in values(Pkg.dependencies())
    info.is_tracking_registry || continue
    toml = joinpath(info.source, "Artifacts.toml")
    isfile(toml) || continue
    for (name, meta) in select_downloadable_artifacts(toml; platform=HostPlatform(), include_lazy=false)
        push!(artifacts, "$(info.name)/$name" => meta)
    end
end
server = Pkg.pkg_server()
sources = asyncmap(artifact -> source(server, artifact.second), artifacts; ntasks=32)
println(JSON.json(Dict(first(artifact) => s for (artifact, s) in zip(artifacts, sources))))
