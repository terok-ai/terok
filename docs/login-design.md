# Login Feature — Design

> [!WARNING]
> This documentation was written by an AI agent and is inaccurate. 

## Problem

terok manages containerized AI coding agent tasks. Users need to open interactive
shells inside running containers — to debug, inspect, or interact with the agent
directly. Without the login feature, this would require manually typing
`podman exec -it <name> bash`, remembering container names, and losing sessions on disconnect.

## Requirements

### R1: One-command login

A single command (`terok login <project> <task>`) should open an interactive shell.
No container name needed — terok resolves it from project/task metadata.

### R2: Persistent sessions

Sessions should survive disconnects. Reconnecting should reattach to the same session
with all state (running processes, environment, working directory) preserved.

### R3: TUI integration

The TUI should allow login without leaving the interface. When possible, the login
session should open in a separate window/tab so the TUI remains usable.

### R4: Work across environments

Users run terok in diverse environments. CLI login should work well in the
terminal ones: terminal (bare), terminal (under tmux), desktop (GNOME, KDE).
Under a web-served TUI (`terok-web` / `textual serve`) there is no host
terminal to attach to, so CLI login is refused with a notification — toad
mode (an in-browser session) is the web-served login path.

### R5: Minimize cognitive load for nested tmux

Host-level tmux (for managing TUI windows) and container-level tmux (for session
persistence) use different prefix keys and visual indicators to avoid confusion.

## Architecture

### Container layer: tmux for session persistence (R2)

Every container ships tmux with a custom config (`/etc/tmux.conf`). The login command
is always the same regardless of how the user reaches the container:

    podman exec -it <container> tmux new-session -A -s main

`-A` means "attach if session exists, create if not." This is idempotent — first login
creates, subsequent logins reattach.

### Host layer: environment-aware terminal delivery (R3, R4)

The login command is the same; what varies is how the user gets a terminal to run it.
A web-served TUI is refused up front (see "Web mode" below); for a local-terminal TUI
a dispatch chain selects the best available method:

    ┌─────────────────────────────────────────────────────────┐
    │ 1. Inside tmux?  → tmux new-window                      │
    │ 2. Desktop DE?   → gnome-terminal / konsole / ptyxis    │
    │ 3. Fallback      → suspend TUI, run directly            │
    └─────────────────────────────────────────────────────────┘

Methods 1-2 keep the TUI visible. Method 3 (suspend) blocks the TUI but works in
any real terminal.

### tmux UX: visual disambiguation (R5)

Two independent tmux servers coexist: one on the host, one in the container. They are
in different PID namespaces and do not interact. The only overlap is the prefix key,
which is resolved by using different defaults:

| Level | Prefix | Status bar | Color |
|---|---|---|---|
| Host | ^b (default) | `HOST tmux (^b)` | Blue |
| Container | ^a (custom) | `CONTAINER tmux (^a)` | Green |

The container status bar cross-references the host prefix (`host: ^b`) so the user
always knows how to switch context. Color provides instant visual identification.

### CLI: top-level command (R1)

`terok login <project> <task>` replaces the current process with
`podman exec -it <container> tmux new-session -A -s main` via `os.execvp()`.
Validation (task exists, has been run, container is running) happens before exec.

### TUI: `l` keybind with dispatch (R3)

The TUI calls `get_login_command()` to get the validated command, then `launch_login()`
to dispatch via the chain above. The return value indicates which method was used,
and the TUI shows a notification or falls back to suspend accordingly.

### Web mode: CLI login refused (R4)

When the TUI is served via `textual serve` (`terok-web`), there is no host terminal:
`suspend()` would kill the served session, and a browser tab cannot attach to a host
process. `_launch_terminal_session` is hard-gated on `App.is_web` — under a web-served
TUI it shows an error notification ("run a task in toad mode") and does nothing else.
Toad mode is the web-served alternative: the task serves its own session in the browser,
no host shell required. (An earlier iteration spawned `ttyd` for a browser-tab terminal;
that was dropped — see issue #473 — because it is a CLI login, not the toad-URL login,
and the goal is a single coherent web-served login story.)

### `--tmux` opt-in wrapper (R3, R5)

`terok tui --tmux` wraps the TUI in a managed tmux session with the host config
(blue status bar, usage hints). Login sessions become additional tmux windows.
This is opt-in — without the flag, the TUI runs directly in the terminal as before.
The global `tui.default_tmux` config setting flips the default so the flag is
implied; `--no-tmux` overrides it for one launch.

The wrapper uses `tmux new-session -A -s terok`, so a second `--tmux` launch
**attaches** to the running `terok` session rather than erroring on the
duplicate name. `--new-session` opts out, dropping `-s` so tmux auto-names a
parallel session.
