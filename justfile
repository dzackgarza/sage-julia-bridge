# sage-julia-bridge - QC delegation (contract: ~/ai-review-ci/justfiles/sage.just setup)

# ai-review-ci contract variables consumed by doctor and workflow installers.
ai_review_ci_schema_version := "1"
ai_review_ci_profile := "sage"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"

default:
    @just --list

# Three-tier QC per the ai-review-ci Sage wiring contract.
test-commit:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . test-commit

test-push:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . test-push

test-ci:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . test-ci

test: test-push

# Full bootstrap: Python package into Sage + Julia deps/artifacts + Oscar verification.
setup: preflight install julia-deps

# Assert required base toolchains are on PATH; fail loudly if any is missing.
preflight:
    #!/usr/bin/env bash
    set -euo pipefail
    missing=()
    for tool in sage julia uv; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        echo "preflight failed: not on PATH: ${missing[*]}" >&2
        exit 1
    fi
    echo "preflight ok: sage julia uv"

# Install the bridge into Sage's Python environment.
install:
    sage -python -m pip install -e .

# Instantiate the Julia environments (bridge project + shared env artifacts)
# and verify the worker's dependencies and Oscar load together.
julia-deps:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{justfile_directory()}}
    julia --startup-file=no --history-file=no --color=no -e '
        using Pkg
        Pkg.instantiate()
        Pkg.precompile()
    '
    julia --project=src/sage_julia_bridge/julia_env --startup-file=no --history-file=no --color=no -e '
        using Pkg
        Pkg.instantiate()
        Pkg.precompile()
        import JSON
        using Oscar
        println("JSON ", pkgversion(JSON), ", Oscar ", Oscar.VERSION_NUMBER, " loaded")
    '

build:
    uv build
