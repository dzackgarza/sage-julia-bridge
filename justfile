ai_review_ci_schema_version := "1"
ai_review_ci_profile := "sage"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"

[doc("List available recipes")]
default:
    @just --list

[doc("Run the commit-tier QC gate")]
test-commit:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . test-commit

[doc("Run the push-tier QC gate")]
test-push:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . test-push

[doc("Run the CI-tier QC gate")]
test-ci:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . test-ci

[doc("Run the push-tier QC gate")]
test: test-push

[doc("Full bootstrap: Python package into Sage + Julia deps/artifacts + Oscar verification.")]
setup: preflight install julia-deps

[doc("Assert required base toolchains are on PATH; fail loudly if any is missing.")]
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

[doc("Install the bridge into Sage's Python environment.")]
install:
    #!/usr/bin/env bash
    set -euo pipefail
    sage_python="$(sage -c 'import sys; print(sys.executable)')"
    uv pip install --python "${sage_python}" -e .

[doc("Resolve the transport and Oscar in the worker project, then verify they load together.")]
julia-deps:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{justfile_directory()}}
    julia --project=src/sage_julia_bridge/julia_env --startup-file=no --history-file=no --color=no -e '
        using Pkg
        Pkg.instantiate()
        Pkg.precompile()
        import JSON
        using Oscar
        println("JSON ", pkgversion(JSON), ", Oscar ", Oscar.VERSION_NUMBER, " loaded")
    '

[doc("Build the source and wheel distributions")]
build:
    uv build

[doc("Run the scheduled whole-tree audit (semgrep and debt scans) through the central implementation.")]
ambient:
    @just -f ~/ai-review-ci/justfiles/sage.just -d . ambient
