# Agent Guide (terok)

## Purpose

`terok` orchestrates and instruments containerized AI coding agents using Podman. It ships both a CLI (`terok`) and a Textual TUI (`terok-tui`). The hardened container runtime (shield, gate, SSH, podman lifecycle) is provided by the `terok-sandbox` package.

## Technology Stack

- **Language**: Python 3.12+
- **Package Manager**: Poetry
- **Container Runtime**: Podman
- **Testing**: pytest with coverage
- **Linting/Formatting**: ruff
- **Module Boundaries**: tach (enforced in CI via `tach.toml`)
- **Documentation**: MkDocs with Material theme
- **TUI Framework**: Textual

## Repo layout

- `src/terok/`: Python package
  - `cli/`, `tui/`: the two presentation frontends
  - `lib/api.py`: the single stable import boundary the frontends consume
  - `lib/domain/`: Project/Task aggregates, ssh/auth workflows, panic, vault
  - `lib/orchestration/`: container lifecycle — `tasks/` (metadata, lifecycle,
    queries) and `task_runners/` (cli/toad/headless/restart mode runners) are
    packages of focused submodules behind a re-export `__init__`
  - `lib/core/`: foundation types, config, pure utilities
  - `lib/integrations/`: thin adapters that own every import from the sibling
    wheels — `executor.py`, `sandbox.py`, `clearance.py`
- `tests/`: `pytest` test suite
- `docs/`: user + developer documentation
- `examples/`, `completions/`: sample configs and shell completions

## Build, Lint, and Test Commands

**During development — ALWAYS use the fast loop:**
```bash
make test-fast # Only the tests affected by your branch diff (tach impact analysis)
```
Rerunning the full suite after every edit is the single biggest time sink in
agent dev loops — don't do it. Iterate with `make test-fast`; run the full
`make test` exactly once, right before committing. One exception: impact
analysis follows the Python import graph only, so after changing non-Python
inputs (`resources/` templates, YAML, shell scripts) `make test-fast` skips
tests that are actually affected — run the full `make test` for those changes.

**Before committing:**
```bash
make lint      # Run linter (required before every commit)
make format    # Auto-fix lint issues if lint fails
make test      # Full unit suite — once, after iterating with test-fast
```

**Before pushing:**
```bash
make test         # Run full test suite with coverage
make tach         # Check module boundary rules (tach.toml)
make lint-imports # Check cross-package import boundaries
make docstrings   # Check docstring coverage (minimum 95%)
make reuse        # Check REUSE (SPDX license/copyright) compliance
make security     # Run bandit SAST scan (no medium/high findings allowed)
make check        # Run all checks (equivalent to CI)
```

**When `pyproject.toml` changes** (added/removed/changed dependencies):

```bash
poetry lock --no-update   # Regenerate lockfile without upgrading existing deps
make install-dev          # Apply the updated lockfile to your local environment
# Commit both pyproject.toml and poetry.lock together
```

**Other useful commands:**
```bash
make install-dev  # Install all development dependencies
make docs         # Serve documentation locally
make clean        # Remove build artifacts
make spdx NAME="Real Human Name" FILES="src/terok/new_file.py"  # Add SPDX header
```

## Coding Standards

- **Style**: Follow ruff configuration in `pyproject.toml`
- **Line length**: 100 characters (ruff formatter target; `E501` is disabled so long strings that cannot be auto-wrapped are tolerated)
- **Imports**: Sorted with isort (part of ruff)
- **Type hints**: Use Python 3.12+ type hints
- **Docstrings**: Required for all public functions, classes, and modules (enforced by `docstr-coverage` at 95% minimum in CI)
- **Cross-references in docstrings**: use mkdocstrings autoref syntax `` [`Name`][module.path.Name] `` — never the Sphinx ``:class:`Name``` / ``:func:`name``` forms. Sphinx roles render as literal text on the rendered docs site (mkdocstrings doesn't process them). Prefer the explicit full path over the bare `` [`Name`][] `` autoref form: explicit paths keep `properdocs build --strict` green even when the symbol's short name isn't unique. For external symbols, use the dependency's own path (e.g. `` [`Sandbox`][terok_sandbox.Sandbox] ``, `` [`StreamReader`][asyncio.StreamReader] ``) — those resolve via the inventories listed in `properdocs.yml`.
- **Testing**: Add tests for new functionality; maintain coverage
- **SPDX headers**: Every source file (`.py`, `.sh`, etc.) must have an SPDX header. Use `make spdx` to add or update it — it handles both new files and existing files correctly:
  ```bash
  make spdx NAME="Real Human Name" FILES="path/to/file.py"
  ```
  - **New file** → creates the header:
    ```python
    # SPDX-FileCopyrightText: 2025 Jiri Vyskocil
    # SPDX-License-Identifier: Apache-2.0
    ```
  - **Existing file** → adds an additional copyright line (preserves the original):
    ```python
    # SPDX-FileCopyrightText: 2025 Jiri Vyskocil
    # SPDX-FileCopyrightText: 2026 New Contributor
    # SPDX-License-Identifier: Apache-2.0
    ```
  When modifying an existing file, always run `make spdx` with the contributor's name to add their copyright line. NAME must be a real person's name (ASCII-only), not a project name. Use a single year (year of first contribution), not a range. Ask the user for their name if unknown. Files covered by `REUSE.toml` glob patterns (`.md`, `.yml`, `.toml`, `.json`, etc.) do not need inline headers. `make reuse` checks compliance but does not generate headers.
- **Emojis**: Must be natively wide (`East_Asian_Width=W`) — no VS16 (U+FE0F) sequences. Use `render_emoji()` from `terok.lib.util.emoji` for aligned output. See `docs/developer.md` → "Emoji width constraints" for details
- **No magic literals**: Never use literal IPs, URLs, ports, or filesystem paths directly in code. Define them as named constants and import from there — `tests/constants.py` for test code, appropriate module-level constants for production code. This centralises magic values and makes future changes trivial. In tests, mock filesystem paths must use a subdirectory under `MOCK_BASE` (e.g. `/tmp/terok-testing/...`) — never `/tmp` directly
- **Test path isolation**: tests must never touch the operator's real filesystem. All on-disk fixtures route through `tmp_path` / `tmp_path_factory`. The autouse `_isolate_user_paths` fixture in `tests/unit/conftest.py` redirects `HOME` and the `XDG_*` chain to a fresh tmp dir, so default-config code paths (`SandboxConfig()`, `handle_*(cfg=None)`) land in tmp by construction — don't bypass it, and add to `_TEROK_PATH_OVERRIDE_ENV_VARS` when introducing a new `TEROK_*_DIR` knob. Integration tests are covered by the analogous `terok_env` fixture in `tests/integration/conftest.py`
- **Public API surface**: `__init__.py` + `__all__` is the contract. Symbols listed in `__all__` are stable across minor releases; anything underscore-prefixed or absent from `__all__` is internal and may change without notice. Review the list before each release — stable APIs stay small because growing them costs.

## Development Workflow

1. Make changes in appropriate module (`src/terok/`)
2. Run `make lint` and `make test-fast` frequently during development
3. Add/update tests in `tests/` directory
4. Run the full `make test` once, before committing
5. If you added or changed cross-module imports, run `make tach` to verify module boundary rules
6. Update documentation in `docs/` if needed
7. Run `make check` before pushing

## Key Guidelines

- **Container Readiness**: When modifying init scripts or server startup, preserve readiness markers (see `docs/developer.md`)
- **Security Modes**: Understand online vs gatekeeping modes when working with git operations
- **Agent Instructions**: When modifying container setup (Dockerfile templates, init scripts, installed tools), check if `src/terok/resources/instructions/default.md` needs updating
- **Minimal Changes**: Make surgical, focused changes
- **Existing Tests**: Never remove or modify unrelated tests
- **Dependencies**: Use Poetry for dependency management; avoid adding unnecessary dependencies

## External Package Dependencies

terok depends on five sibling packages, each pinned to a GitHub release wheel:

```text
terok ──┬─> terok-executor ──> terok-sandbox ─┬─> terok-shield     (egress firewall)
        ├─> terok-sandbox     (also direct)   └─> terok-clearance  (operator prompts)
        ├─> terok-clearance   (also direct)
        ├─> terok-shield      (also direct — CLI registry bridge only)
        └─> terok-util        (shared foundation)
```

`terok-util` sits below the whole stack — executor, sandbox, shield, and clearance all depend on it. `terok-shield` and `terok-clearance` have no terok-* dependencies besides `terok-util`.

All five siblings are **explicit** dependencies in `pyproject.toml` — even though sandbox is pulled transitively via executor, and clearance is also pulled by sandbox. This is intentional: terok imports (or, for shield, CLI-bridges) directly from each, so the dependency must be declared, not just inherited.

**Version sync rules:**

- When bumping `terok-sandbox` in terok, the same version must be pinned in `terok-executor`. Otherwise Poetry will reject conflicting URL pins. Bump terok-executor first, release it, then bump both in terok.
- When bumping `terok-clearance` in terok, the same version must be pinned in `terok-sandbox` (for the same reason). Bump terok-sandbox first, release it, then bump both in terok.
- When bumping `terok-shield`: terok pins the wheel URL directly (for the `terok shield` CLI bridge), while terok-sandbox uses a version range — update terok's pin, and terok-sandbox only when the new version falls outside its range.
- `terok-util` is range-pinned by all four siblings but URL-pinned in terok; bump terok's pin directly, and touch the siblings only when util's compatible range changes.
- After any version bump: run `poetry lock` and commit both `pyproject.toml` and `poetry.lock`.

**Import convention:** never import a sibling wheel directly. Every `terok_executor` / `terok_sandbox` / `terok_clearance` symbol is re-exported through a thin adapter in `src/terok/lib/integrations/` (`executor.py`, `sandbox.py`, `clearance.py`) — import from there (`from terok.lib.integrations.executor import X`). This is enforced by `.importlinter`'s `*-boundary` contracts: a direct `from terok_sandbox import …` outside `lib/integrations/` fails CI. When a sibling release adds a symbol terok needs, extend the adapter's re-export surface rather than reaching past it. Two exceptions: terok-shield is never imported from the library — only the `terok shield` CLI command bridges to shield's own CLI registry (via `lib/integrations/shield.py`); and terok-util is a foundation library meant for direct import (`from terok_util import …`) with no adapter and no import-linter contract.

When a sibling exposes a needed symbol only from an internal submodule (e.g. `terok_sandbox.commands._handle_vault_seal`), the adapter is still the single place that reach is allowed to live — but the proper fix is to get the sibling to promote it to public API.

## Module Boundaries (tach)

The project uses [tach](https://github.com/gauge-sh/tach) to enforce module boundary rules defined in `tach.toml`. Each module declares its allowed dependencies and public interface. The layers, top to bottom, are `presentation` → `domain` → `orchestration` → `core` → `integrations`. When adding new cross-module imports:

- If importing from an existing dependency, ensure the symbol is in that module's `[[interfaces]]` `expose` list
- If adding a new dependency between modules, add it to the `depends_on` list and update `[[interfaces]]` as needed
- Run `make tach` (or `tach check`) to verify; CI will reject boundary violations

**Boundary tools are design signal.** A long `.importlinter` allowed-importer list or a tach violation usually means the API is leakier than it should be — find the missing abstraction, don't hide it behind a facade.

## SonarCloud

The project is analyzed by [SonarCloud](https://sonarcloud.io/summary/new_code?id=terok-ai_terok) on every push to master. Unlike CodeRabbit (which posts actionable PR comments), SonarCloud findings often require triage — many are low-priority style issues or false positives rather than real bugs. Treat them as input for decisions, not as a checklist.

**Fetching new issues for a PR** (replace `PR_NUMBER`):
```bash
curl -s 'https://sonarcloud.io/api/issues/search?projects=terok-ai_terok&pullRequest=PR_NUMBER&issueStatuses=OPEN&ps=100' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
for i in d.get('issues',[]):
    sev=i['severity']; rule=i['rule']; msg=i.get('message','')
    comp=i['component'].split(':',1)[-1]; line=i.get('textRange',{}).get('startLine','?')
    print(f'[{sev}] {rule}: {msg}')
    print(f'  {comp}:{line}')
"
```

**Fetching recent issues on the main branch** (replace date as needed):
```bash
curl -s 'https://sonarcloud.io/api/issues/search?projects=terok-ai_terok&issueStatuses=OPEN&createdAfter=2026-03-01&ps=100&s=CREATION_DATE&asc=false' \
  | python3 -c "import json,sys; [print(f'[{i[\"severity\"]}] {i[\"rule\"]}: {i.get(\"message\",\"\")}\n  {i[\"component\"].split(\":\",1)[-1]}:{i.get(\"textRange\",{}).get(\"startLine\",\"?\")}') for i in json.load(sys.stdin).get('issues',[])]"
```

No authentication is needed (public project). The PR number can be found in the GitHub PR URL or from `gh pr view --json number`.

## Git & GitHub Workflow

- **Upstream repo**: `terok-ai/terok` (canonical; PRs target here)
- **Origin repo**: `sliwowitz/terok` (fork; branches are pushed here)
- **Remote setup**: `upstream` = `terok-ai/terok`, `origin` = `sliwowitz/terok`
- **PR target**: Always open PRs against `upstream/master` (`terok-ai/terok:master`)
- **Branch workflow**: Create feature branch locally, rebase on `upstream/master`, push to `origin`, open PR against `upstream/master`
- **Branch naming**: Use conventional prefixes: `feat/`, `fix/`, `chore/`, `docs/`
- **Commit messages**: Use [Conventional Commits](https://www.conventionalcommits.org/) format (`feat:`, `fix:`, `chore:`, `docs:`, etc.)
- **Issue tracker**: GitHub Issues on `terok-ai/terok`

```bash
# Typical PR workflow
git fetch upstream
git checkout -b feat/my-feature upstream/master
# ... make changes, commit ...
git push origin feat/my-feature
gh pr create --repo terok-ai/terok --base master --head sliwowitz:feat/my-feature
```

## Important Files

- `docs/developer.md`: Detailed architecture and implementation guide
- `docs/usage.md`: Complete user documentation
- `Makefile`: Build and test automation
- `pyproject.toml`: Project configuration and dependencies
- `tach.toml`: Module boundary rules (enforced in CI)


## Dependency Pinning & `pyproject.toml` Hygiene

**Version pinning policy.** Runtime/production dependencies — those pulled in
by a plain `pip install` / `pipx install` of this package (the
`[project].dependencies` table) — are pinned by the dependency's major
version:

- **Third-party, major 0 (`0.y.z`)** → pin to an **exact patch**
  (`pkg==0.y.z`). Pre-1.0 packages promise no compatibility across either
  minors *or* patches, so a floating range invites silent breakage.
- **Third-party, major ≥ 1** → **compatible-release at the tested
  baseline**: `pkg~=X.Y` where `X.Y` is the locked major.minor (floor =
  what we test against, cap = next major). Use the patch-series form
  `pkg~=X.Y.Z` only where a specific patch floor is required — note the
  PEP 440 truncation rule: the cap is one level above the last written
  component (`~=2.13` → `<3`, `~=8.2.5` → `<8.3`). Prefer `~=` over a
  hand-rolled `>=,<` pair: it states the baseline as one fact with the
  ceiling derived by construction, so the bounds cannot drift apart.
- **Sibling `terok-*` deps** → `~=0.y.z` (or their release-wheel URL pin).
  We guarantee patch-level API stability across the sibling packages, so
  the patch-series form is exactly right — do *not* exact-pin them (it
  would fight the multi-repo release/PR-chain flow).

Dev / test / docs / tooling dependencies (the `[tool.poetry.group.*]` groups)
are **exempt** — they are not shipped to installers and exact-pinning them is
an unwarranted maintenance burden the developers can absorb. After changing
any pin, run `poetry lock` and commit `pyproject.toml` and `poetry.lock`
together.

**Comment discipline in `pyproject.toml`.** The dependency tables stay
comment-free and self-documenting, apart from the standing policy pointer
above them. **Never** comment on why a dependency -- especially a sibling
`terok-*` package -- is pinned a certain way, and never mention dev-cycle
state (temporary git-branch pins, the multi-repo PR chain): cross-repo
merges are performed by a script that does not understand comments, so any
such note is carried straight into a production release. Keep pin
rationale in commit messages, PR descriptions, or this file. Ordinary
explanatory comments in `[tool.*]` sections are fine. `pyproject.toml`
stays ASCII-only.
