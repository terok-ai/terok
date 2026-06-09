# SELinux & the socket transport

> [!WARNING]
> This documentation was written by an AI agent and might be inaccurate.

Since 0.7.3, terok defaults to **`services.mode: socket`** — Unix-socket
IPC between host services (gate, vault, SSH-agent) and task containers.
No TCP ports are claimed for terok's own services.

This page explains what that means on your distro, and how to opt out.

## What changes per distro

### Non-SELinux distros (Ubuntu, Debian, Arch, Alpine, …)

Nothing extra.  `terok setup` installs socket-mode units, services bind
Unix sockets, containers mount the sockets with `:z`, and everything
works.  You will never see a SELinux block in the setup output and you
never need `sudo`.

### SELinux distros in permissive mode

Same as above.  Sockets bind normally, the default container-SELinux
policy covers the flow, `terok setup` skips the SELinux block.

### SELinux distros in enforcing mode (Fedora, RHEL, …)

By default SELinux blocks `container_t → unconfined_t` `connectto` on
Unix sockets (see [Dan Walsh][1] / [Podman #23972][2]).  To let rootless
Podman containers reach terok's host-side sockets, we ship a narrowly
targeted policy module (`terok_socket_t`) that carves out this single
exception.  Installing it is a one-time `sudo` operation per host.

`terok setup` on an enforcing host will print:

```text
SELinux:
  terok_socket_t   WARN (policy NOT installed)
                   Containers cannot connect to service sockets.
                   Fix (pick one):
                     install policy: sudo bash /path/to/install_policy.sh
                     or opt out:     add `services: {mode: tcp}` to ~/.config/terok/config.yml
```

The installer script is short, auditable, and sits next to the `.te`
policy source in the terok-sandbox package.  `cat` it before running.
It compiles `terok_socket.te` with `checkmodule` / `semodule_package`
and loads it with `semodule -i`.

After running it, `terok setup` shows `ok (policy installed)` and task
containers can connect to the gate / vault / SSH-agent sockets.

#### Removing the policy

```bash
sudo semodule -r terok_socket
```

## Opting out: the TCP transport

If you can't or don't want to install the policy — shared host where
you don't have root, locked-down distro image, container build where
`sudo` isn't practical — set:

```yaml
# ~/.config/terok/config.yml
services:
  mode: tcp
```

This falls back to the previous TCP-loopback transport.  terok claims
three auto-allocated TCP ports per user (gate, vault, ssh-agent) and
containers reach them via `host.containers.internal` / slirp4netns.
Works on any distro, SELinux or not, zero extra setup.

The TCP transport is **not** deprecated — it's a supported opt-out.
Some caveats:

- Three ports per user are visible on the host via `ss -tlnp`
  (127.0.0.1 only).  They don't leak off-loopback, but on multi-user
  hosts another user could see that *something* is listening.
- Port-allocation edge cases (collisions with other services that
  bind in the 18700-32700 range) surface as terok setup failures with
  clear messages.

## Background

The socket story is an intermediate step toward a longer-term **bridge
mode** where task containers and terok service containers sit on a
shared rootless Podman bridge — container↔container connections don't
cross the SELinux host boundary, automatic MCS categories provide
per-task isolation, and no custom policy module is needed at all.
Bridge mode is tracked separately and depends on
[terok-shield](https://github.com/terok-ai/terok-shield) support.

[1]: https://danwalsh.livejournal.com/78643.html
[2]: https://github.com/containers/podman/discussions/23972
