# Container and Image Lifecycle

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
├── Workspace    ~/.local/share/terok/tasks/<project>/v9krt/workspace-dangerous/
├── Metadata     ~/.local/share/terok/projects/<project>/tasks/v9krt.yml
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
    │       │        task run          │              │
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
| `task restart` | `podman stop` → `podman start` | `podman start` | Error: suggests `task run` |
| `task status`  | Shows state            | Shows state     | Shows "not found"      |
| `task delete` | `podman rm -f` + cleanup | `podman rm -f` + cleanup | Cleanup only |

### Container Naming

```text
<project-id>-<mode>-<task-id>

Examples:
  myproject-cli-v9krt   # CLI container for task v9krt
  myproject-auth-codex  # Auth container (ephemeral, uses --rm)
```

### Ephemeral vs Persistent Containers

| Type | Containers | Lifetime | `--rm` flag |
|------|------------|----------|-------------|
| Task | `*-cli-*` | Persistent | No |
| Auth | `*-auth-*` | Ephemeral | Yes |

Task containers persist to allow:
- Fast restart (`podman start` vs full `podman run`)
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
│ L0: terok-l0:<base-tag>                                         │
│ ┌───────────────────────────────────────────────────────────────┐ │
│ │ Ubuntu 24.04 + common tools (git, ssh, vim, ripgrep, ...)     │ │
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
│ │ + SSH_KEY_NAME            │ │
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
Container image hash ≠ Current project build hash
        │
        ▼
  "Image: old" warning in TUI
        │
        ▼
  User should: terok project build <project>
               then: task delete + task run
               or:   task stop + podman rm <container> + task run
```

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
terok task restart myproject v9krt  # Fast restart (podman start)
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
