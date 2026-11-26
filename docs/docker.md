# Running terok in Docker

> [!WARNING]
> This documentation was written by an AI agent and might be inaccurate.

> **Experimental — not tested regularly.**
> This mode is intended for local evaluation only.

terok-in-Docker runs the full terok stack — web TUI, terok-sandbox
runtime (gate server, shield), and rootless Podman — inside a single
Docker container.  This lets you try terok without installing Podman on
your host.

## Quick start

```bash
docker build -t terok-in-docker .

docker run -d --privileged --network host \
  --name terok \
  terok-in-docker
```

Open <http://localhost:8566> in your browser.

`--privileged` is required for nested rootless Podman (user namespaces
and cgroup delegation).  `--network host` is the simplest setup: all
ports bind directly to the host with no `-p` mapping needed.

## Bridge networking

If you prefer Docker's default bridge network, map ports explicitly.
Note: agent web task (toad) ports are auto-allocated from the shared
port registry (default range 18700–32700; narrow it with
`network.port_range_start` / `network.port_range_end` in config.yml),
so they won't be reachable from the host unless you pin the range and
map it too.

```bash
docker run -d --privileged \
  -p 8566:8566 \
  --name terok \
  terok-in-docker
```

## Persistent state

By default, all state is lost when the container is removed.  Mount
volumes to preserve terok config, task state, and Podman images/containers:

```bash
docker run -d --privileged --network host \
  -v ~/terok-in-docker/config:/home/podman/.config/terok \
  -v ~/terok-in-docker/share:/home/podman/.local/share/terok \
  -v ~/terok-in-docker/containers:/home/podman/.local/share/containers \
  --name terok \
  terok-in-docker
```

| Mount | Persists |
|-------|----------|
| `.config/terok` | Projects, global config |
| `.local/share/terok` | Task metadata, gate state, workspaces |
| `.local/share/containers` | Podman images and containers (avoids re-pulling/rebuilding) |

The entrypoint automatically fixes ownership of mounted directories.

## LAN / reverse proxy access

The web TUI binds to `0.0.0.0` (the entrypoint passes
`--host 0.0.0.0` to `terok-web`), so it is LAN-reachable out of the box
with `--network host`. That is plain HTTP: the Basic-auth password and
the tokens in task links cross the network readable, which is acceptable
on a trusted LAN only. Outside the image, terok refuses a non-loopback
listener or a non-loopback `TEROK_PUBLIC_HOST` unless
`TEROK_ALLOW_CLEARTEXT=1` is set; the entrypoint sets it, because the
image exists to be reached over the network. For anything beyond a
trusted LAN, put a TLS reverse proxy in front.

To make the TUI's WebSocket links and toad URLs display the correct
external address, set `TEROK_PUBLIC_URL` and optionally
`TEROK_PUBLIC_HOST`:

```bash
-e TEROK_PUBLIC_URL=http://myserver:8566
-e TEROK_PUBLIC_HOST=myserver
```

Behind nginx with TLS:

```bash
-e TEROK_PUBLIC_URL=https://terok.example.com
-e TEROK_PUBLIC_HOST=terok.example.com
```

## Git gate access from host

The gate no longer runs as a standalone host server: it is served per
task by each container's supervisor over a Unix socket, with per-task
tokens.  The startup log line about a "gate admin token" and the
`TEROK_GATE_ADMIN_TOKEN` / `TEROK_GATE_BIND` variables are leftovers
from the retired host gate daemon and have no effect.

To reach a project's gate mirror from inside the terok container, use
the `file://` URL printed by:

```bash
docker exec -it -u podman terok terok project gate-path myproject
```

## Interactive shell

To get a shell instead of the web TUI:

```bash
docker run -it --privileged --network host --name terok \
  terok-in-docker bash
```

To exec into a running container:

```bash
docker exec -it -u podman terok bash
```

The `-u podman` is required because the container starts as root (to fix
bind-mount ownership) and then drops to `podman` internally.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TEROK_PUBLIC_URL` | Browser-facing URL for the web TUI (passed to `terok-web --public-url` by the entrypoint) |
| `TEROK_PUBLIC_HOST` | Hostname/IP advertised in toad URLs (default `127.0.0.1`); a non-loopback value also publishes the toad ports on all interfaces |
| `TEROK_ALLOW_CLEARTEXT` | Allow plain HTTP beyond loopback for `terok-web` and toad links (the entrypoint sets `1`; a trusted LAN only, otherwise use a TLS reverse proxy) |

## Known limitations

**Toad web access from LAN:** Toad containers run inside nested
rootless Podman, which uses pasta for port forwarding.  Pasta only
forwards connections arriving on `127.0.0.1`, so toad is reachable
from the Docker host but not from other LAN machines.  The web TUI
is unaffected (it runs directly in the Docker container's process
space).  A future reverse-proxy integration (nginx) may resolve this.

## Architecture

```text
┌─ Docker (host) ────────────────────────────────────────────┐
│  terok-in-docker container                                  │
│  ├─ terok-web (TUI served on :8566)                        │
│  ├─ Podman (rootless, uid 1000, fuse-overlayfs)            │
│  │  ├─ agent-container-1                                   │
│  │  ├─ agent-container-2                                   │
│  │  └─ ...                                                 │
│  └─ terok config + state in /home/podman/.config|.local/   │
└─────────────────────────────────────────────────────────────┘
```
