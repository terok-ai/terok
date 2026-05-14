#!/bin/bash
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Multi-distro integration test runner for terok.
#
# Builds test containers for each target distro and runs the
# integration suite inside them. Requires a modern host with
# podman and privileges to run nested containers.
#
# Usage:
#   ./tests/containers/run-matrix.sh               # run all distros
#   ./tests/containers/run-matrix.sh debian12      # run one distro
#   ./tests/containers/run-matrix.sh --build-only  # build images only
#   ./tests/containers/run-matrix.sh --list        # list available distros
#   ./tests/containers/run-matrix.sh --no-cache    # force full rebuild
#
# The host must support nested podman (rootless or --privileged).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE_PREFIX="terok-test"
SOURCE_MOUNT="/src"
WORKSPACE_DIR="/workspace"
PYTHON_VERSION="3.12"
TEROK_DIAGNOSTIC_COMMAND="poetry run terok config"

# Host-side scratch dir each test container writes its observed podman
# version to.  Surfaced after each distro and in the final summary so the
# matrix report shows what was actually exercised, not just what we expected.
RESULTS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/terok-matrix-XXXXXX")"
chmod 0777 "$RESULTS_DIR"
trap 'rm -rf "$RESULTS_DIR"' EXIT

# distro name -> observed podman version (populated by run_tests).
declare -A ACTUAL_VERSIONS=()

# ── Terminal colors (disabled when stdout is not a tty) ──
if [[ -t 1 ]]; then
    C_BOLD='\033[1m'
    C_CYAN='\033[1;36m'
    C_YELLOW='\033[1;33m'
    C_GREEN='\033[1;32m'
    C_RED='\033[1;31m'
    C_DIM='\033[2m'
    C_RESET='\033[0m'
else
    C_BOLD='' C_CYAN='' C_YELLOW='' C_GREEN='' C_RED='' C_DIM='' C_RESET=''
fi

# Target distros: name -> Containerfile suffix
declare -A DISTROS=(
    [debian12]="debian12"
    [ubuntu2404]="ubuntu2404"
    [ubuntu2604]="ubuntu2604"
    [debian13]="debian13"
    [fedora43]="fedora43"
    [fedora44]="fedora44"
    [podman]="podman"
    [nix]="nix"
)

# The ``nix`` slot runs the same flavour the GitHub-Actions host CI
# does — full unit suite plus host-only integration tests — but under
# Nix-wrapped Python.  No podman / nftables, so the multi-phase
# ``run_tests`` flow doesn't apply; we route through ``run_nix_tests``
# instead.  Use of nix isn't an officially-supported install mode;
# the slot exists so wrapped-Python regressions (#717 family) can't
# slip past unit + host-only coverage on a different conceptual
# install setup.
declare -A SLOT_KIND=(
    [debian12]="podman"
    [ubuntu2404]="podman"
    [ubuntu2604]="podman"
    [debian13]="podman"
    [fedora43]="podman"
    [fedora44]="podman"
    [podman]="podman"
    [nix]="nix"
)

# Expected podman versions — pinned to the exact distro-shipped point
# release.  We do *not* fail on mismatch (a distro update is a normal
# event), only surface a yellow WARNING so a maintainer can refresh
# the pins.  The ``podman`` image rolls with upstream, so it carries
# no expectation — its observed version is reported as-is.
declare -A EXPECTED_VERSIONS=(
    [debian12]="4.3.1"
    [ubuntu2404]="4.9.3"
    [ubuntu2604]="5.7.0"
    [debian13]="5.4.2"
    [fedora43]="5.8.2"
    [fedora44]="5.8.2"
    [podman]="latest"
    [nix]="n/a"
)

# Print "expected podman X.Y.Z" for distros with a version pin, or
# "podman latest, version pinned by upstream" for the bare ``podman``
# image.  Used in the ``==> Testing ...`` heading and the ``--list``
# output.
version_expectation() {
    local name="$1"
    local expected="${EXPECTED_VERSIONS[$name]:-}"
    case "$expected" in
        latest) printf 'podman latest, version pinned by upstream' ;;
        n/a) printf 'nix-wrapped Python (unit + host-only integration, no podman)' ;;
        *) printf 'expected podman %s' "$expected" ;;
    esac
}

# Print the parenthesised version summary for ``$name`` after a run.
# Match (or ``latest``): dim ``(podman X.Y.Z)``.
# Mismatch: yellow ``(WARNING: expected podman A, got podman B)``.
# Never fails the run — distro point releases are routine, the warning
# is just a nudge to refresh ``EXPECTED_VERSIONS``.
version_summary() {
    local name="$1"
    local expected="${EXPECTED_VERSIONS[$name]:-}"
    local actual="${ACTUAL_VERSIONS[$name]:-?}"
    if [[ "$expected" == "n/a" ]]; then
        printf '%s(nix-wrapped Python %s)%s' "$C_DIM" "$actual" "$C_RESET"
    elif [[ "$expected" == "latest" || "$expected" == "$actual" ]]; then
        printf '%s(podman %s)%s' "$C_DIM" "$actual" "$C_RESET"
    else
        printf '%s(WARNING: expected podman %s, got podman %s)%s' \
            "$C_YELLOW" "$expected" "$actual" "$C_RESET"
    fi
}

# Non-root user baked into each Containerfile (uid 1000).
# The podman image uses its pre-existing 'podman' user.
declare -A TEST_USERS=(
    [debian12]="testrunner"
    [ubuntu2404]="testrunner"
    [ubuntu2604]="testrunner"
    [debian13]="testrunner"
    [fedora43]="testrunner"
    [fedora44]="testrunner"
    [podman]="podman"
    [nix]="testrunner"
)

usage() {
    echo "Usage: $0 [OPTIONS] [DISTRO...]"
    echo ""
    echo "Options:"
    echo "  --build-only   Build images without running tests"
    echo "  --no-cache     Rebuild images from scratch (ignore layer cache)"
    echo "  --list         List available distros"
    echo "  -h, --help     Show this help"
    echo ""
    echo "Default: install full infrastructure, run all integration tests."
    echo ""
    echo "Available distros: ${!DISTROS[*]}"
    return 0
}

warn_keyring() {
    # Warn when the host's containers.conf does not disable kernel keyrings.
    # Matrix runs cycle many containers and can exhaust the per-user 200-key
    # quota, causing misleading "Disk quota exceeded" (EDQUOT) from crun.
    local conf="${CONTAINERS_CONF:-}"
    if [[ -z "$conf" ]]; then
        for candidate in "$HOME/.config/containers/containers.conf" \
                         /etc/containers/containers.conf; do
            [[ -f "$candidate" ]] && conf="$candidate" && break
        done
    fi
    if [[ -z "$conf" ]] || ! grep -qE '^\s*keyring\s*=\s*false' "$conf" 2>/dev/null; then
        echo -e "${C_YELLOW}WARNING: kernel keyring is not disabled in containers.conf"
        echo -e ""
        echo -e "  Matrix tests create many containers and may exhaust the per-user"
        echo -e "  keyring quota (200 keys), causing spurious EDQUOT errors."
        echo -e ""
        echo -e "  Add to ${C_BOLD}~/.config/containers/containers.conf${C_YELLOW}:"
        echo -e ""
        echo -e "    ${C_BOLD}[containers]${C_YELLOW}"
        echo -e "    ${C_BOLD}keyring = false${C_YELLOW}"
        echo -e ""
        echo -e "  See: https://terok-ai.github.io/terok/kernel-keyring/${C_RESET}"
        echo ""
    fi
}

build_image() {
    local name="$1"
    local file="$SCRIPT_DIR/Containerfile.${DISTROS[$name]}"
    local image="$IMAGE_PREFIX:$name"
    local -a build_args=()

    $NO_CACHE && build_args+=(--no-cache)

    echo -e "${C_CYAN}==> Building ${C_BOLD}$image${C_CYAN} from $file${C_RESET}"
    podman build "${build_args[@]}" -t "$image" -f "$file" "$REPO_ROOT"
    return $?
}

run_tests() {
    local name="$1"
    local image="$IMAGE_PREFIX:$name"
    local ctr_name="$IMAGE_PREFIX-$name"
    local test_user="${TEST_USERS[$name]}"

    echo ""
    echo -e "${C_CYAN}==> Testing ${C_BOLD}$name${C_CYAN} ($(version_expectation "$name"))${C_RESET}"
    echo -e "    ${C_DIM}user: $test_user${C_RESET}"
    echo ""

    # Three-phase flow:
    #   Phase 1: tests that do NOT need hooks
    #   Phase 2: install global hooks via terok shield install-hooks --user
    #   Phase 3: tests that need hooks
    #
    # Privileged mode gives the outer container the capabilities needed
    # for nested podman, but tests run as uid 1000 (rootless podman).
    podman run --rm --name "$ctr_name" \
        --privileged \
        --security-opt label=disable \
        --device /dev/fuse:rw \
        -e container=podman \
        -v "$REPO_ROOT:$SOURCE_MOUNT:ro,Z" \
        -v "$RESULTS_DIR:/results:rw,Z" \
        "$image" \
        bash -c "
            set -e

            # ── Prepare workspace (as root) ──
            cp -a $SOURCE_MOUNT $WORKSPACE_DIR
            chown -R $test_user:$test_user $WORKSPACE_DIR

            # Strip IPv6 zone-ID nameservers — they reference host interfaces
            # (e.g. eno1) that don't exist inside the container, causing dig
            # to reject the entire resolv.conf.  Fixed upstream in podman 5.4+
            # (https://github.com/containers/common/pull/2233).
            # Remove once we drop < 5.4 support.
            cp /etc/resolv.conf /tmp/resolv.conf.clean
            grep -v '^nameserver.*%' /tmp/resolv.conf.clean > /etc/resolv.conf

            # ── Run everything as the rootless test user ──
            su - $test_user -c '
                set -e
                export XDG_RUNTIME_DIR=/run/user/\$(id -u)

                cd $WORKSPACE_DIR

                echo \"--- podman version ---\"
                # Capture observed version into the shared /results dir.
                # No single quotes anywhere in this inner block — it is
                # wrapped in a single-quoted su -c argument, so any single
                # quote would close it early.  Parameter expansion only.
                if command -v podman >/dev/null 2>&1; then
                    podman_ver_line=\$(podman --version 2>&1 | head -n1)
                    echo \"\$podman_ver_line\"
                    # podman version 5.8.2 -> 5.8.2
                    echo \"\${podman_ver_line##* }\" > /results/$name.podman-version
                else
                    echo \"podman not available\"
                    : > /results/$name.podman-version
                fi

                echo \"--- rootless podman preflight ---\"
                podman info --format \"podman={{.Version.Version}} storage={{.Store.GraphDriverName}}\" \
                    || { echo \"FATAL: rootless podman not functional\" >&2; exit 1; }

                if command -v uv >/dev/null 2>&1; then
                    uv venv --python $PYTHON_VERSION .venv
                    . .venv/bin/activate
                    uv pip install poetry
                else
                    python\${PYTHON_VERSION} -m venv .venv 2>/dev/null \
                        || python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --quiet --upgrade pip
                    pip install --quiet poetry
                fi

                echo \"--- python version ---\"
                python --version
                # ``stories`` is an optional group — pulls python-dbusmock
                # (and its dbus-python transitive) so the cross-package
                # story tests run.  GitHub-hosted CI skips this group
                # because dbus-python has no wheel and needs system
                # headers we install in the matrix Containerfiles only.
                poetry install --with test --with stories --no-interaction
                echo \"--- deps installed ---\"

                # ── Phase 1: tests without hooks ──
                echo \"\"
                echo \"--- phase 1: tests without hooks ---\"
                poetry run pytest tests/integration/ -v --tb=short -m \"not needs_hooks\"

                # ── Phase 2: install global hooks ──
                echo \"\"
                echo \"--- phase 2: installing shield hooks ---\"
                poetry run terok shield install-hooks --user

                # Verify hooks are detectable — fail fast if setup did not work
                poetry run python3 -c \"
from terok_shield import has_global_hooks
assert has_global_hooks(), \\\"Shield hooks not detected after setup\\\"
print(\\\"Shield hooks verified.\\\")
\"

                # ── Phase 3: tests with hooks ──
                echo \"\"
                echo \"--- phase 3: tests with hooks ---\"
                poetry run pytest tests/integration/ -v --tb=short -m \"needs_hooks\"

                echo \"\"
                echo \"--- terok config ---\"
                $TEROK_DIAGNOSTIC_COMMAND 2>&1 || true
            '
        "

    local status=$?

    # Pick up what the inner script observed (may be empty if podman
    # was missing or the container died before reaching that step).
    local actual
    actual=$(cat "$RESULTS_DIR/$name.podman-version" 2>/dev/null || true)
    ACTUAL_VERSIONS[$name]="${actual:-?}"

    local vsummary
    vsummary=$(version_summary "$name")
    if [[ $status -eq 0 ]]; then
        echo -e "${C_GREEN}==> $name: PASS${C_RESET} $vsummary"
    else
        echo -e "${C_RED}==> $name: FAIL${C_RESET} $vsummary" >&2
    fi
    return "$status"
}

run_nix_tests() {
    # Run the same suite GitHub Actions runs (unit + host-only
    # integration) but under Nix-wrapped Python.  Catches the
    # wrapped-Python regressions (#717 family — every spawn of
    # ``[sys.executable, "-m", "terok…"]`` must thread PYTHONPATH)
    # and any other behaviour that quietly only works on the
    # GitHub-Actions interpreter shape.
    #
    # No podman, no nft, no hooks — the Nix container is intentionally
    # leaner than the multi-distro slots.  Marker-filtered integration
    # mirrors ``make test-integration-host``.
    local name="$1"
    local image="$IMAGE_PREFIX:$name"
    local ctr_name="$IMAGE_PREFIX-$name"

    echo ""
    echo -e "${C_CYAN}==> Testing ${C_BOLD}$name${C_CYAN} ($(version_expectation "$name"))${C_RESET}"
    echo ""

    # Runs as root inside the Nix container — see Containerfile.nix
    # for why (the wrapped-Python failure mode is uid-independent and
    # ``su``/``newuidmap`` plumbing is deferred until podman lands).
    podman run --rm --name "$ctr_name" \
        --security-opt label=disable \
        -v "$REPO_ROOT:$SOURCE_MOUNT:ro,Z" \
        -v "$RESULTS_DIR:/results:rw,Z" \
        "$image" \
        bash -c "
            set -e
            cp -a $SOURCE_MOUNT $WORKSPACE_DIR
            cd $WORKSPACE_DIR

            echo '--- nix-wrapped python ---'
            which python3.12
            python3.12 --version
            python3.12 --version | awk '{print \$2}' > /results/$name.python-version

            # Nix disables user site-packages (PYTHONNOUSERSITE), so
            # ``pip install --user`` is rejected.  Install into a venv
            # instead — same shape the other matrix slots use.  The
            # venv inherits the wrapper's sys.path scrubbing, which is
            # the wrapped-Python failure mode we want to exercise.
            python3.12 -m venv .venv
            . .venv/bin/activate
            pip install --quiet --upgrade pip
            pip install --quiet . pytest pytest-asyncio pytest-cov pytest-tach

            echo ''
            echo '--- unit tests ---'
            pytest tests/unit -v --tb=short

            echo ''
            echo '--- host-only integration tests ---'
            # Same marker filter ``make test-integration-host`` uses on
            # GitHub-Actions: skip everything that wants podman or the
            # internet.  Nix container has neither.
            pytest tests/integration -v --tb=short \
                -m 'needs_host_features and not needs_internet and not needs_podman'
        "

    local status=$?
    local actual
    actual=$(cat "$RESULTS_DIR/$name.python-version" 2>/dev/null || true)
    ACTUAL_VERSIONS[$name]="${actual:-?}"

    local vsummary
    vsummary=$(version_summary "$name")
    if [[ $status -eq 0 ]]; then
        echo -e "${C_GREEN}==> $name: PASS${C_RESET} $vsummary"
    else
        echo -e "${C_RED}==> $name: FAIL${C_RESET} $vsummary" >&2
    fi
    return "$status"
}

BUILD_ONLY=false
LIST_ONLY=false
NO_CACHE=false
TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only) BUILD_ONLY=true ;;
        --no-cache) NO_CACHE=true ;;
        --list) LIST_ONLY=true ;;
        -h|--help) usage; exit 0 ;;
        *) TARGETS+=("$1") ;;
    esac
    shift
done

if $LIST_ONLY; then
    for name in "${!DISTROS[@]}"; do
        echo "$name ($(version_expectation "$name"))"
    done | sort
    exit 0
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    TARGETS=("${!DISTROS[@]}")
fi

for target in "${TARGETS[@]}"; do
    if [[ -z "${DISTROS[$target]+x}" ]]; then
        echo -e "${C_RED}Error: unknown distro '$target'. Available: ${!DISTROS[*]}${C_RESET}" >&2
        exit 1
    fi
done

warn_keyring

for target in "${TARGETS[@]}"; do
    build_image "$target"
done

if $BUILD_ONLY; then
    echo -e "${C_GREEN}Images built.${C_RESET} Use '$0' without --build-only to run tests."
    exit 0
fi

PASSED=()
FAILED=()

for target in "${TARGETS[@]}"; do
    case "${SLOT_KIND[$target]}" in
        nix) runner=run_nix_tests ;;
        *) runner=run_tests ;;
    esac
    if "$runner" "$target"; then
        PASSED+=("$target")
    else
        FAILED+=("$target")
    fi
done

echo ""
echo -e "${C_BOLD}===== Matrix Summary =====${C_RESET}"
for target in "${PASSED[@]}"; do
    echo -e "  ${C_GREEN}PASS${C_RESET}: $target $(version_summary "$target")"
done
for target in "${FAILED[@]}"; do
    echo -e "  ${C_RED}FAIL${C_RESET}: $target $(version_summary "$target")"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
