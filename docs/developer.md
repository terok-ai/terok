# Developer Guide

> [!WARNING]
> This documentation was written by an AI agent and might be inaccurate.

This document covers internal architecture and implementation details for
contributors and maintainers of terok.  For the container/host service
topology it is the authoritative page; user-facing behaviour lives in
[usage.md](usage.md).

## Domain Model Architecture

terok's library layer (`src/terok/lib/`) separates **value objects** (pure
data), **entities** (identity + behavior), and **service functions**
(stateless helpers).

### Object Graph

```text
api.get_project("myproj")  →  Project          (Aggregate Root)
    .config                →  ProjectConfig    (Value Object — dataclass)
    .gate                  →  GitGate          (terok-sandbox)
    .ssh                   →  SSHManager       (terok-sandbox)
    .agents                →  AgentManager     (config-stack resolution)
    .get_task("1")         →  Task             (Entity)
        .meta              →  TaskMeta         (Value Object)
```

### Key Types

| Type | Module | Role |
|------|--------|------|
| `Project` | `lib/domain/project.py` | Aggregate root; entry point for all project-scoped operations.  Wraps `ProjectConfig` with behavior. |
| `Task` | `lib/domain/task.py` | Entity; wraps `TaskMeta` with lifecycle methods (`run_cli`, `stop`, `restart`, `delete`, `rename`, `logs`, …). |
| `ProjectConfig` | `lib/core/project_model.py` | Value object loaded from `project.yml`.  No I/O. |
| `TaskMeta` | `lib/orchestration/tasks/` | Task metadata snapshot, persisted as a dossier + meta file pair (see below). |
| `GitGate` | `terok_sandbox` (via `lib/integrations/sandbox.py`) | Manages the bare git mirror; wraps the git CLI. |
| `SSHManager` | `terok_sandbox` (via `lib/integrations/sandbox.py`) | Generates SSH keypairs; private keys are served by the vault SSH signer, never mounted. |
| `AgentManager` | `lib/domain/project.py` | Resolves layered agent configuration and provider selection. |

### Design Principles

**Snapshot semantics.** `Task` captures a point-in-time snapshot of
`TaskMeta`.  Mutations (`rename()`, `run_cli()`, `stop()`) update persistent storage but
do *not* refresh the in-memory snapshot — obtain a fresh `Task` via
`project.get_task(id)` to observe new state.  This keeps entities free of
implicit I/O.

**Lazy initialization.** `Project` subsystems (`gate`, `ssh`, `agents`) are
created on first access, not at construction.  `Project` uses `__slots__`,
so `cached_property` is unavailable; the manual pattern
(`if self._gate is None: ...`) is used instead.

**Identity-based equality.** `Project.__eq__` compares by project name;
`Task.__eq__` by `(project_name, task_id)`.  Both are hashable.

**Atomic persistence.** Every on-disk state write (task meta, build
manifest, work status) goes through a temp-file + `os.replace()` rename so
interrupted writes leave parseable state.  Task metadata is split into
`<task>_dossier.json` (the wire shape other packages consume: project,
task, name) and `<task>_meta.yml` (terok bookkeeping: mode, workspace,
ports, exit code) — see `lib/orchestration/tasks/meta.py`.

### Module Boundaries

Layers, top to bottom: `presentation` → `domain` → `orchestration` →
`core` → `integrations` (tach-enforced via `tach.toml`).

```text
presentation (terok.cli, terok.tui)
    └── terok.lib.api — the single stable import boundary
          ├── terok.lib.domain.*          Project/Task aggregates, ssh/auth, panic, vault access
          ├── terok.lib.orchestration.*   tasks, task_runners, image, environment, ports
          ├── terok.lib.core.*            config, paths, project model, task state
          └── terok.lib.integrations.*    the only modules allowed to import sibling wheels
```

`terok.lib.api` is a pure re-export front door split into focused
submodules (`api.task`, `api.project`, `api.vault`, `api.gate`,
`api.shield`, `api.agents`, `api.clearance`, `api.setup`,
`api.ssh_routing`) plus a process-wide `Config` snapshot.  Presentation code imports from `api`
only; new TUI/CLI features that need internal modules should extend `api`
first.  Sibling-wheel imports outside `lib/integrations/` fail CI
(`.importlinter` `*-boundary` contracts); `terok_util` is the exception —
a foundation library imported directly.

---

## Container Readiness and Log Streaming

terok streams a starting container's logs to the user and detaches once a
readiness condition is met.

### CLI mode (`task run --mode cli`)

The container command is
(`lib/orchestration/task_runners/cli.py`):

```bash
bash -lc 'init-ssh-and-repo.sh && echo __CLI_READY__; tail -f /dev/null'
```

Readiness markers, in order of appearance:

- `">> init complete"` — emitted by `init-ssh-and-repo.sh`
  (terok-executor, `resources/scripts/`); terok-sandbox exports the same
  string as `READY_MARKER`.
- `"__CLI_READY__"` — echoed after the init script succeeds.

The host follows logs via the container handle's `stream_initial_logs()`
([`Container`][terok_sandbox.runtime.protocol.Container] protocol) and
detaches when either marker appears or after a 60 s timeout.
On timeout the container keeps running; `podman logs -f <container>`
continues to work.

**If you modify the init script**, preserve a stable readiness line or
update the detection in `lib/orchestration/task_runners/cli.py`.

---

## Container Layer Architecture

terok builds project containers in three layers:

| Layer | Image tag | Built by | Purpose |
|-------|-----------|----------|---------|
| L0 | `terok-l0:<base-tag>` | terok-executor | Development base (distro, git, ssh, `dev` user) |
| L1 | `terok-l1-cli:<base-tag>[-<agents>]` | terok-executor | Agent CLIs per roster selection |
| L2 | `<project>:l2-cli` | terok | Project-specific config and user snippet |

L0/L1 are project-agnostic and cache well; L2 is the thin per-project
layer.  The unsuffixed L1 tag is a *default alias* pointing at the last
build of the user's configured default agent selection; explicit
selections get a sorted `-a-b-c` suffix so they coexist in the image
store.  Dockerfile templates live in terok-executor
(`l0.dev.Dockerfile.template`, `l1.agent-cli.Dockerfile.template`) and
terok (`l2.project.Dockerfile.template`); rebuild staleness is detected
via per-layer content hashes recorded in `build_manifest.json`
(`lib/orchestration/image.py`).

See [container-layers.md](container-layers.md) for build commands and
cache-busting flags.

---

## Container Lifecycle

Containers follow podman's own lifecycle, split across the three layers:

- **terok-sandbox** exposes podman's verbs on the `Sandbox` facade —
  `run`/`create`, `start` (rebuilds the reboot-wiped `/run/terok` mount
  source first), `stop` (halts and retains), `rm` (force-removes;
  host-side state is the wiring layer's pairing via
  `remove_container_state`).  `RunSpec.ephemeral` maps to `--rm`.
- **terok-executor** is `podman run` for one agent: containers are
  retained after exit, the workspace lives in the container's writable
  layer (repo cloned in through the gate) unless `--workspace` mounts a
  host directory, and podman itself is the only container registry —
  per-container host state sits at `state_root()/run/<name>`, derived
  from the name.
- **terok** owns tasks (container + workspace + metadata).  The mode
  runners (`task_runners/cli.py`, `toad.py`) only ever *create*;
  everything that brings a container back goes through one ladder in
  `task_runners/restart.py`: resume the existing container, else
  recreate it in place through the normal launch path (same names,
  fresh tokens, config re-read, per-task settings from metadata).
  `task_restart` is the ladder's bounce flavor (stop-if-running first);
  a plain restart resumes the container as-is and an image-ID drift
  probe only *warns* that the image is stale (long-running tasks keep
  their in-container state), while `--recreate` (`fresh=True`) forces
  the recreate rung to pick up a rebuilt image.  `ensure_task_running`
  is the attach flavor (a running container is reported, never
  disturbed).  Headless tasks never take the recreate rung — that would
  replay their original prompt.

Invariants: the task workspace is never re-seeded on relaunch (the
new-task-marker protocol decides), podman is the source of truth for
run state (no running/stopped status is persisted in task metadata —
only markers such as `exit_code` and `ready_at`), and `task delete`
is the only teardown of task state.

---

## Volume Mounts and Environment Variables

Mount and env assembly is shared with headless/standalone launches:
terok-executor's `assemble_container_env()` builds the base environment
and the per-agent shared config mounts (definitions come from the agent
roster), and terok layers project concerns on top
(`lib/orchestration/environment.py`).  The full mount table is documented
in [shared-dirs.md](shared-dirs.md).  SSH keys are **not** mounted — the
vault's SSH signer serves them (see below).

### Core variables (always set)

| Variable | Set by | Value / purpose |
|----------|--------|-----------------|
| `PROJECT_NAME` | terok | Project name from config |
| `TASK_ID` | executor | Task identifier |
| `REPO_ROOT` | executor | `/workspace` — init script clone target |
| `CLAUDE_CONFIG_DIR` | executor | `/home/dev/.claude` |
| `GIT_RESET_MODE` | executor (terok can override via host `TEROK_GIT_RESET_MODE`) | Workspace reset behavior, default `none` |
| `TEROK_GIT_AUTHORSHIP` | executor | Maps human/agent identities onto git author/committer (terok's default: `agent-human`) |
| `HUMAN_GIT_NAME` / `HUMAN_GIT_EMAIL` | executor | Human git identity (from terok config or host git config) |
| `TEROK_UNRESTRICTED` | executor | `1` when the task runs unrestricted (see permission modes) |

### Conditional variables (security mode)

| Variable | When set | Purpose |
|----------|----------|---------|
| `CODE_REPO` | Project has an upstream or an active gate | Clone/origin URL — gate URL in gatekeeping mode, upstream in online mode |
| `GIT_BRANCH` | Alongside `CODE_REPO` when a default branch is configured | Target branch |
| `CLONE_FROM` | Online mode with a usable gate | Gate URL as a faster initial clone source |
| `EXTERNAL_REMOTE_URL` | Relaxed gatekeeping (`gatekeeping.expose_external_remote`) | Upstream URL added as the `external` remote |
| `TEROK_GATE_TOKEN` | Gate wired | Per-task token the gate server validates |

With no `upstream_url` and `gate.enabled: false`, `CODE_REPO` is unset and
the workspace starts empty.

## Security Modes

- **Online** — `CODE_REPO` points at upstream; the gate (if present) only
  seeds the initial clone.
- **Gatekeeping** — `CODE_REPO` points at the gate; pushes land in the
  host-side mirror for human review.

See [git-gate-and-security-modes.md](git-gate-and-security-modes.md).

---

## Host-Side Service Architecture

All host-side services — vault token broker, SSH signer, clearance hub,
verdict helper, git gate — run inside a single **per-container
supervisor** process.  There are no persistent daemons and no systemd
units: when no containers run, `pgrep terok` is empty.  (`terok-sandbox
setup` sweeps systemd units left behind by pre-supervisor releases.)

### Per-container supervisor

terok-sandbox installs an OCI hook pair (`createRuntime` + `poststop`)
that matches on the `terok.sandbox.sidecar` annotation.  The annotation
carries the absolute path of a **sidecar JSON** the launch path writes
before `podman run`; the hook spawns (via a rendered wrapper)
`terok-sandbox supervisor <container-id> <sidecar>`, which reads its
entire identity (project, task, vault DB
path, gate mirror path + token, transport mode, ports) from that file —
no environment guessing.  The supervisor awaits `podman wait
<container-id>` and tears everything down when the container exits; the
`poststop` hook reaps stragglers.

Services come up in dependency order
(`terok_sandbox/supervisor/main.py`):

1. **Verdict helper** — [`VerdictServer`][terok_clearance.VerdictServer],
   a varlink wrapper that execs `terok-shield allow|deny`.
2. **Clearance hub** — [`ClearanceHub`][terok_clearance.ClearanceHub],
   fan-out point for shield events; holds a client to the verdict helper.
3. **Git gate** (when wired) — [`GateServer`][terok_sandbox.gate.server.GateServer]
   serving git smart-HTTP against `<gate_base_path>/<project>.git`,
   validating the per-task `TEROK_GATE_TOKEN`.  Started before the vault
   because the container's entrypoint clones immediately.
4. **Vault token broker** — [`VaultProxy`][terok_sandbox.vault.daemon.token_broker.VaultProxy],
   substitutes real credentials for phantom tokens.
5. **Vault SSH signer** — ssh-agent protocol backed by the vault DB.
6. **Desktop notifier** — an [`EventSubscriber`][terok_clearance.EventSubscriber]
   turning `connection_blocked` events into D-Bus popups; degrades to a
   no-op when no session bus exists.

### Socket layout

Per-container endpoints, all on the host:

| Path | Keyed by | Contents |
|------|----------|----------|
| `$XDG_RUNTIME_DIR/terok/sandbox/run/<container-name>/` | container name | `vault.sock`, `ssh-agent.sock`, `gate-server.sock` — **bind-mounted into the container at `/run/terok/`** |
| `$XDG_RUNTIME_DIR/terok/clearance/<short-id>.sock` | 12-char container ID | varlink socket operator UIs subscribe to |
| `$XDG_RUNTIME_DIR/terok/events/<short-id>.sock` | 12-char container ID | line-JSON ingest socket the shield NFLOG reader pushes to |
| `$XDG_RUNTIME_DIR/terok/verdict/<short-id>.sock` | 12-char container ID | varlink verdict helper |
| `$XDG_RUNTIME_DIR/terok/control/<short-id>.sock` | 12-char container ID | reserved |

The name-keyed directory exists because the launch path must pre-create
and bind-mount it before podman assigns a container ID; the cross-package
sockets use the short ID because the shield reader keys on it.  Operator
UIs (the TUI clearance screen, standalone `terok clearance`) multiplex
all containers via
[`MultiSocketSubscriber`][terok_clearance.MultiSocketSubscriber] —
sockets appear and disappear with their supervisors.

Host tools that need the gate repository don't go through the supervisor:
they read the mirror clone directly (`terok project gate-path <name>`
prints it).  The in-supervisor HTTP gate exists to serve confined
containers, whose egress shield blocks everything else.

### Socket vs TCP transport — `services.mode`

```yaml
# config.yml
services:
  mode: socket   # default; alternative: tcp
```

Read by [`SandboxConfig.services_mode`][terok_sandbox.config.SandboxConfig.services_mode].
In `socket` mode the container-facing services (vault broker, SSH signer,
gate) bind Unix sockets in the per-container directory above.  In `tcp`
mode they bind per-container host loopback ports (allocated via the
sandbox port registry, recorded in the sidecar) reached over
`host.containers.internal`.  Host-only services (clearance hub, verdict
helper) always use Unix sockets.

Socket mode is preferred.  TCP exists for SELinux-enforcing hosts without
root: a bind-mounted host socket is unreachable from `container_t` until
the bundled policy (which labels the sockets `terok_socket_t`) is
installed; with root, `terok setup` prints the install command and socket
mode works.  TCP's trade-off: loopback ports are visible to every local
user.

### The container-side bridge

`terok_sandbox/resources/bridges/ensure-bridges.sh` is sourced by the
container entrypoint and per-shell init (so bridges self-heal after a
restart).  It reads env vars injected at create time and starts up to
four `socat` bridges so agents see uniform endpoints in both modes:

| Endpoint (in container) | Socket mode | TCP mode |
|---|---|---|
| `$SSH_AUTH_SOCK` = `/tmp/ssh-agent.sock` | socat → mounted signer socket (token handshake via `ssh-agent-bridge.sh`) | socat → signer loopback port |
| `http://localhost:$TEROK_VAULT_LOOPBACK_PORT` (vault HTTP) | socat → `/run/terok/vault.sock` | socat → `host.containers.internal:$TEROK_TOKEN_BROKER_PORT` |
| vault Unix socket | `/run/terok/vault.sock` (the mount itself) | `/tmp/terok-vault.sock` (socat → broker port) |
| `http://localhost:9418` (git) | socat → `/run/terok/gate-server.sock` | socat → `host.containers.internal:$TEROK_GATE_PORT` |

The gate bridge uses `socat … retry=30,interval=1` so a clone racing the
supervisor's gate bind waits instead of failing.  Bridge liveness is
tracked via PID files under `/tmp/.terok/` — stale socket files are not
trusted as liveness sentinels.

### Shield (no host service)

`terok-shield` runs no host-side service.  It installs two OCI hook
pairs (`createRuntime` / `poststop`): the nft hook applies the
pre-generated nftables ruleset inside the container's network namespace
and starts/reaps the per-container dnsmasq; the separate bridge hook
spawns the NFLOG reader that translates kernel rejects into
`connection_blocked` events on the supervisor's events socket.  The nft
hook is **fail-closed**: a failure prevents the container from starting.
The reader hook and the supervisor hook are deliberately **soft-fail**
(a container without vault/gate still starts — the shield independently
denies its egress).

### Other per-task plumbing

| Service | Activation | Endpoint |
|---|---|---|
| ACP host proxy | bound on first `terok acp connect` | per-task Unix socket (`acp_socket_path()` in `lib/core/paths.py`); aggregates the in-container `terok-*-acp` wrappers behind one JSON-RPC endpoint |
| Workspace | plain `podman -v` bind mount | `<task workspace dir>` ↔ `/workspace` |

---

## Agent Permission Mode Architecture

> **See also:** [Agent Configuration Compatibility Matrix](agent-compat-matrix.md)
> for the per-agent reference of CLI flags, env vars, config files, and
> ACP adapter behaviour.

Agents run **unrestricted** (auto-approve) or **restricted**
(vendor-default permissions).  One decision, one carrier, per-agent
translation at the edge:

```text
CLI flag (--unrestricted / --restricted)
  │  ↓ if not given
Config stack: global → project   (agent-config resolution)
  │  ↓ if not configured
Default: unrestricted
  │
  ▼
TEROK_UNRESTRICTED=1   ← single decision carrier in the container env
  │
  ▼  per-agent translation, defined in the roster YAML (auto_approve:)
  ├─ env vars   (e.g. VIBE_BYPASS_TOOL_PERMISSIONS=true, OPENCODE_PERMISSION='{"*":"allow"}')
  ├─ CLI flags  (e.g. Codex --yolo) injected by the generated shell wrappers
  └─ config files (Claude: /etc/claude-code/managed-settings.json, written by init-ssh-and-repo.sh)
```

The host never injects flags into agent command lines directly — agents
are launched through bash wrappers generated from terok-executor's
`agent-wrappers.sh.j2` at image build time, and the wrappers check
`$TEROK_UNRESTRICTED` at runtime.  The resolved boolean is persisted to
task meta so `task status` can display it.

**Adding a new agent:** declare an `auto_approve:` block (env vars and/or
flags) in the agent's roster YAML in terok-executor.  Nothing in terok
changes.

### In-container `hilfe`

The in-container `hilfe` command ships from terok-executor
(`resources/scripts/hilfe`); the L1 login banner calls
`_TEROK_LOGIN=1 hilfe --kurz`.  When changing agent availability, mount
behavior, or rebuild terminology, keep in sync: `hilfe`, the welcome hook
in `l1.agent-cli.Dockerfile.template` (terok-executor), and
[usage.md](usage.md).

---

## Agent Instructions Architecture

Two-layer pattern, resolved by terok-executor
(`provider/instructions.py`, `resolve_instructions()`):

1. **YAML `instructions` key** — selects the base (bundled default,
   custom string, or a list).  Absent = bundled default.
2. **Standalone `instructions.md`** in the project root — always appended
   after the YAML chain.  Purely additive.

In list form, the `_inherit` sentinel is *spliced* — replaced with the
bundled default content at that position — so projects can compose
default + overrides + addenda.  Task runners pass the project root so the
standalone file is picked up.  The TUI badge distinguishes `default`,
`custom + inherited`, and `custom only`.

---

## Development Workflow

### Initial Setup

```bash
git clone git@github.com:terok-ai/terok.git
cd terok
make install-dev
```

### Before you commit / push

```bash
make lint      # before every commit; make format auto-fixes
make test      # alias for make test-unit (fast suite with coverage)
make tach      # after changing cross-module imports
make check     # everything CI runs: lint + test + tach + lint-imports + typecheck + security + docstrings + deadcode + reuse
```

Integration tests live under `tests/integration/`:

```bash
make test-integration-host     # filesystem/process workflows, no podman/network
make test-integration-network  # network-dependent
make test-integration-podman   # podman-dependent
make test-integration          # all of the above
make test-integration-map      # regenerate docs/test_map.md
make test-matrix               # cross-distro matrix (needs nested podman; BUILD_ONLY=1 to just build)
make ci-map                    # regenerate docs/ci_map.md
```

Security/quality extras: `make security` (bandit), `make sonar-inputs`,
`make docstrings`, `make deadcode`, `make typecheck`, `make complexity`,
`make reuse`.

### Running from Source

```bash
export TEROK_CONFIG_DIR=$PWD/examples
export TEROK_STATE_DIR=$PWD/tmp/dev-runtime/var-lib-terok

python -m terok.cli project list
python -m terok.tui
```

### TUI Notes

#### Emoji width constraints

Rich/Textual and terminal emulators disagree on the width of emojis using
Variation Selector-16 (U+FE0F): Rich counts 2 cells, most terminals render
1, which breaks Textual layout.  Rules:

1. Only **natively wide** emojis (`East_Asian_Width=W`,
   `Emoji_Presentation=Yes`); no VS16 sequences.
2. Never use emoji literals in code — define them in the central display
   dicts (`STATUS_DISPLAY`, `MODE_DISPLAY`, `SECURITY_CLASS_DISPLAY`,
   `GPU_DISPLAY` in `lib/core/task_display.py`; `WORK_STATUS_DISPLAY` in
   `lib/core/work_status.py`) and render via `render_emoji()` from
   `lib/util/emoji.py`.
3. Guard tests in `tests/unit/lib/test_emoji.py` fail CI on VS16 emojis
   and on missing labels for `--no-emoji` mode.

See the `lib/util/emoji.py` module docstring for background and the
terminal developments to watch.

#### Web-compatible workflows (no suspended terminal)

The TUI must work served over the web (`terok-web`), where there is no
terminal to suspend to.  Actions that used to print to a suspended
terminal run as captured child processes instead:

- **`tui/console_log.py`** — `ConsoleLogRegistry` holds in-memory log
  entries; `dispatch_console_action()` runs a referenced callable in a
  child process and pumps its merged output into the entry (app-scoped,
  survives hidden viewers, never pushes a screen).
- **`tui/worker_actions.py`** — one thin child-process entrypoint per
  dispatched TUI action; add new actions here.
- **`WorkerLogScreen`** — live tail view over an entry;
  `ConsoleOutputScreen` lists all entries.
- Interactive workflows are native widgets (`tui/text_screens.py` for
  instructions, `tui/clearance_screen.py` for shield verdicts).
- Web detection: prefer `App.is_web`; CLI container login is hard-gated
  off in web mode.

---

## Making a Release

1. Update `version` in `pyproject.toml`
2. Commit `release: bump version to X.Y.Z` and merge to `master`
3. Create tag `vX.Y.Z` on GitHub (Releases → New release → Generate notes)

The release workflow on `v*` tags builds the wheel/sdist and attaches
them.  Between releases, `poetry-dynamic-versioning` derives PEP 440
versions from git; the TUI title bar shows `vX.Y.Z+` past a release and
`vX.Y.Z` at the tag.
