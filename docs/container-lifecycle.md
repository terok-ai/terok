# Container and Image Lifecycle

> [!WARNING]
> This documentation was written by an AI agent and might be inaccurate.

## Overview

terok manages two types of resources:
- **Images** — immutable, built once, shared across tasks
- **Containers** — mutable instances of images, one per task

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                              IMAGES (immutable)                              │
│   ┌─────────┐     ┌─────────────┐     ┌─────────────────────────┐            │
│   │   L0    │ ──▶ │     L1      │ ──▶ │           L2            │            │
│   │  (dev)  │     │   (cli)     │     │      (project-cli)      │            │
│   └─────────┘     └─────────────┘     └─────────────────────────┘            │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                            CONTAINERS (mutable)                              │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐      │
│  │ project-cli-v9krt  │  │ project-cli-h4y12  │  │ project-cli-k3v8h  │      │
│  │   (task v9krt)     │  │   (task h4y12)     │  │   (task k3v8h)     │      │
│  └────────────────────┘  └────────────────────┘  └────────────────────┘      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Container Lifecycle

### Task = Workspace + Metadata + Container

A task consists of three persistent components:

```text
Task v9krt
├── Workspace    ~/.local/share/terok/sandbox-live/tasks/<project>/v9krt/workspace-dangerous/
├── Metadata     ~/.local/share/terok/core/projects/<project>/tasks/v9krt_dossier.json + v9krt_meta.yml
└── Container    <project>-cli-v9krt
```

All three persist independently and survive:
- Container stops
- Machine reboots
- terok restarts

### Container States

```text
                    ┌──────────────────┐
                    │   (not exists)   │
                    └────────┬─────────┘
                             │
                         task run
                       (first time)
                             │
                             ▼
    ┌────────────────────────────────────────────────────┐
    │                                                    │
    │  ┌──────────┐   task stop    ┌──────────────────┐  │
    │  │ RUNNING  │ ──────────────▶│ STOPPED / EXITED │  │
    │  │          │                │                  │  │
    │  │          │◀────────────── │                  │  │
    │  └──────────┘  task restart  └──────────────────┘  │
    │       │                             │              │
    │       │                             │              │
    │       └──────────┬──────────────────┘              │
    │                  │                                 │
    │             task delete                            │
    │                  │                                 │
    │                  ▼                                 │
    │         ┌──────────────┐                           │
    │         │   REMOVED    │                           │
    │         └──────────────┘                           │
    │                                                    │
    └────────────────────────────────────────────────────┘
```

### CLI Commands

| Command | Container Exists & Running | Container Exists & Stopped | Container Doesn't Exist |
|---------|---------------------------|---------------------------|------------------------|
| `task run` | Always creates a fresh task + container (new ID) | Always creates a fresh task + container (new ID) | Always creates a fresh task + container (new ID) |
| `task stop` | `podman stop` | Error: not running | Error: not running |
| `task restart` | `podman stop`, then resume as-is (warns on image drift) — or recreate on `--recreate` | Resume (`podman start`) as-is (warns on image drift) — or recreate on `--recreate` | Recreates the container in place (same task ID, name, workspace) |
| `task status`  | Shows state            | Shows state     | Shows "not found"      |
| `task delete` | `podman rm -f` + cleanup | `podman rm -f` + cleanup | Cleanup only |

`task restart` is a resume-or-recreate ladder: it resumes the existing
container when it can — keeping it as-is even when the project image was
rebuilt underneath it, so a long-running task keeps its in-container
state.  A stale image is only *warned* about (pointing at recreate +
restart), not upgraded.  When the resume rung is gone — the container no
longer exists, or podman refuses to start it — it recreates through the
normal launch path (workspace kept, gate token reused).  `--recreate`
skips straight to that rung, the explicit upgrade that picks up a
rebuilt image.  Headless tasks are the exception — recreating would
replay their original prompt, so a missing container is an error there.

### Container Naming

```text
<project-name>-<mode>-<task-id>

Examples:
  myproject-cli-v9krt   # CLI container for task v9krt
  myproject-auth-codex  # Auth container (ephemeral, uses --rm)
  host-auth-codex       # Host-wide auth container (no project scope)
```

### Ephemeral vs Persistent Containers

| Type | Containers | Lifetime | `--rm` flag |
|------|------------|----------|-------------|
| Task | `*-cli-*` | Persistent | No |
| Auth | `*-auth-*` | Ephemeral | Yes |

Task containers persist to allow:
- Fast resume (`podman start` vs full `podman run`)
- Preserved in-container state (apt installs, pip packages, shell history)
- Consistent task = workspace + metadata + container model

Auth containers are ephemeral because:
- One-time authentication flow
- No state to preserve
- Clean up automatically after use

---

## Image Lifecycle

### Build Hierarchy

```text
┌───────────────────────────────────────────────────────────────────┐
│ L0: terok-l0:<base-tag>                                           │
│ ┌───────────────────────────────────────────────────────────────┐ │
│ │ Distro base (default fedora:44) + git, ssh, vim, ripgrep, ... │ │
│ │ + dev user + /workspace                                       │ │
│ └───────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────┐
│ L1: terok-l1-cli:<base-tag>   │
│ ┌───────────────────────────┐ │
│ │ + Codex CLI               │ │
│ │ + Claude Code             │ │
│ │ + GitHub Copilot          │ │
│ │ + Mistral Vibe            │ │
│ │ + OpenCode (blablador)    │ │
│ └───────────────────────────┘ │
└───────────────────────────────┘
                │
                ▼
┌───────────────────────────────┐
│ L2: <project>:l2-cli          │
│ ┌───────────────────────────┐ │
│ │ + Project-specific env    │ │
│ │ + CODE_REPO, GIT_BRANCH   │ │
│ │ + User snippet            │ │
│ └───────────────────────────┘ │
└───────────────────────────────┘
```

### Build Commands

| Command | What it builds | When to use |
|---------|---------------|-------------|
| `terok project build <project>` | L2 only | Normal use (reuses L0/L1) |
| `terok project build <project> --refresh-agents` | L0 + L1 + L2 | Bust the agent-install cache |
| `terok project build <project> --full-rebuild` | L0 + L1 + L2 (no cache) | Refresh base image + system packages |
| `terok project build <project> --agents <list>\|all` | L0 + L1 + L2 | One-shot override of which agents bake into L1 |
| `terok project build <project> --dev` | + L2-dev image | Manual debugging container |

### Image Staleness Detection

The TUI detects when a task's container uses an outdated image:

```text
Container's build-context hash ≠ current build hash
        │
        ▼
  "Image: old" warning in TUI
        │
        ▼
  User should: terok project build <project>
               then: terok task restart <project> <task> --recreate
```

Staleness is detected by comparing per-layer build-context hashes
(`build_manifest.json`, with the `terok.build_context_hash` image label
as fallback) — not raw image IDs.  A plain `task restart` resumes the
container as-is and *warns* when the image has drifted, leaving a
long-running task on its existing image; `--recreate` picks up the
rebuilt image by recreating the container in place.  The workspace is
kept either way — no need to delete the task.

---

## Quick Reference

### Starting a Task

```bash
# Create a fresh task + container in one step (default on a TTY: also attaches)
terok task run myproject

# Re-attach a shell into a running task (prefix accepted if unambiguous)
terok login myproject v9krt
```

### Stopping and Restarting

```bash
terok task stop myproject v9krt     # Graceful stop (container persists)
terok task restart myproject v9krt  # Resume (or recreate in place if needed)
```

### Checking Status

```bash
terok task status myproject v9krt   # Shows metadata vs actual container state
terok task list myproject           # Lists all tasks with status
```

### Cleaning Up

```bash
terok task delete myproject v9krt   # Removes container + workspace + metadata
```

### Manual Container Management

```bash
# These work because containers persist
podman ps -a --filter name=myproject     # List all project containers
podman logs myproject-cli-v9krt          # View container logs
podman exec -it myproject-cli-v9krt bash # Enter running container
podman stop myproject-cli-v9krt          # Stop container
podman start myproject-cli-v9krt         # Start stopped container
podman rm myproject-cli-v9krt            # Remove container (keeps workspace)
```
