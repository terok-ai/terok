# SELinux & the socket transport

> [!WARNING]
> This documentation was written by an AI agent and might be inaccurate.

Since 0.7.3, terok defaults to **`services.mode: socket`** — Unix-socket
IPC between the per-container host services (gate, vault, SSH signer)
and task containers.  No TCP ports are claimed for terok's own services.

This page explains what that means on your distro, and how to opt out.

## What changes per distro

### Non-SELinux distros (Ubuntu, Debian, Arch, Alpine, …)

Nothing extra.  The per-container supervisor services bind Unix sockets,
containers mount them with `:z`, and everything works.  You will never
see a SELinux block in the setup output and you never need `sudo`.

### SELinux distros in permissive mode

Same as above.  Sockets bind normally, the default container-SELinux
policy covers the flow, `terok setup` skips the SELinux block.

### SELinux distros in enforcing mode (Fedora, RHEL, …)

By default SELinux blocks `container_t → unconfined_t` `connectto` on
Unix sockets (see [Dan Walsh][1] / [Podman #23972][2]).  To let rootless
Podman containers reach terok's host-side sockets, we ship a narrowly
targeted policy module (`terok_socket`, defining the `terok_socket_t`
socket type) that carves out this single exception.  Installing it is a one-time `sudo` operation per host.

`terok setup` on an enforcing host reports the policy stage as MISSING
and ends with an "SELinux policy required" hint that gives both fixes:
install the policy with `terok setup selinux` — it shows the exact
`sudo` command and, on request, the policy rules before anything runs —
or opt out by adding `services: {mode: tcp}` to
`~/.config/terok/config.yml`.  `terok sickbay` reports the same
condition as a warning.

The installer script is short, auditable, and sits next to the `.te`
policy source in the terok-sandbox package.  `terok setup selinux`
prints its exact path in the `sudo` command before anything runs (and
`--show` prints the compiled rules), so you can `cat` it first.
It compiles `terok_socket.te` with `checkmodule` / `semodule_package`
and loads it with `semodule -i`.

After running it, `terok setup` reports the policy as installed and task
containers can connect to the gate / vault / SSH-signer sockets.

#### Removing the policy

```bash
sudo semodule -r terok_socket
```

## Optional Wayland proxy policy

The base `terok_socket` policy is not a desktop-access grant. Projects using
`run.wayland: wl-mitm` can additionally install the separate optional
Wayland module:

```bash
terok setup selinux --with-wayland --show
terok setup selinux --with-wayland
```

The installer still installs the base socket policy. Without `--with-wayland`
its behavior is unchanged; omitting the flag does not remove an optional
module installed earlier. The flag is only valid for `setup selinux`.
The operator installs wl-mitm separately.

The optional module supplies a dedicated domain for the stock wl-mitm
process. It does not grant containers access to all `unconfined_t` host
sockets and does not change the real compositor socket's label. The proxy
uses its own listener and forwards file descriptors required for rendering.
An installed policy whose domain transition fails does not trigger an
unconfined retry.

This policy is defense in depth, not endpoint-specific IPC isolation.
Desktop socket labels can be shared by unrelated host sockets, and the
filesystem confinement helper does not mediate Unix-socket connections.
A compromised proxy must not be assumed unable to reach every other host
service. The protocol allowlist remains a separate control.

If SELinux, AppArmor or ordinary file permissions deny access, the task
continues with a warning and GUI access is unavailable. Terok checks from
inside the container; seeing a socket on the host alone does not prove it
is usable. No policy is installed automatically during launch. Administrator
policies may already permit the connection without this optional module.

### Direct desktop access is a separate opt-in

The proxy policy does not enable `wl-direct`. Install the independent
`terok_wayland_direct` module explicitly:

```bash
terok setup selinux --with-wayland-direct --show
terok setup selinux --with-wayland-direct
```

The two flags can be combined; neither implies the other. Direct policy
compilation additionally needs `make`, `m4`, `selinux-policy-devel` and
`container-selinux`. The base socket and filtered-proxy policies compile
directly with `checkmodule`; the direct policy uses the distro's
container and virt baseline interfaces to retain normal container confinement,
including execution of host-mounted init helpers. These interfaces need the
SELinux development Makefile and container interfaces.
These are **host build-time prerequisites**, not dependencies of task startup.
The installer checks for them before compiling any module and reports missing
files with package-install hints.

On Fedora Workstation:

```bash
sudo dnf install selinux-policy-devel container-selinux make m4
```

On Silverblue, install missing packages **on the host**, not in Toolbox.
If only the development Makefile is missing:

```bash
sudo rpm-ostree install --apply-live selinux-policy-devel
terok setup selinux --with-wayland-direct
```

Add other missing prerequisite packages to that install command if needed.
If pending deployment changes prevent live application, install without
`--apply-live`, reboot into the new deployment, then retry the policy setup.
See the [rpm-ostree package-layering documentation](https://coreos.github.io/rpm-ostree/administrator-handbook/#live-applying-package-additions).

Only newly created `wl-direct` tasks select `terok_wayland_direct_t`.
This domain reuses Fedora's container baseline, audited against Fedora 45,
and keeps Podman's per-task MCS categories. Existing host booleans still
control conditional grants, including NVIDIA device access; the module does
not enable them or mount GPU devices. Ordinary containers receive no additional
desktop permissions. Neither mode relabels the host compositor socket.

**Both modes increase host attack surface.** Direct mode adds no protocol
filter. Its SELinux permissions cover reachable host sockets with the same
labels, not uniquely the compositor; the single-socket mount and container
namespaces remain essential restrictions.

After a package update changes this policy, rerun
`terok setup selinux --with-wayland-direct` to replace the loaded module.
Updating the Python package alone does not update the host's SELinux policy.
An existing container retains its creation-time domain: recreate it if it
was created before the direct policy was installed. Containers already using
`terok_wayland_direct_t` receive updated rules without recreation.
Before removing `terok_wayland_direct`, stop its containers; restarting them
afterward requires restoring the module or recreating them. Terok never falls
back to `label=disable`.

Switching other services to TCP does not bypass Wayland's socket permissions.

## Opting out: the TCP transport

If you can't or don't want to install the policy — shared host where
you don't have root, locked-down distro image, container build where
`sudo` isn't practical — set:

```yaml
# ~/.config/terok/config.yml
services:
  mode: tcp
```

This falls back to the TCP-loopback transport.  Each container launch
allocates three kernel-assigned loopback ports (vault broker, SSH
signer, gate); containers reach them via `host.containers.internal`.
Works on any distro, SELinux or not, zero extra setup.

The same section works per project — in a project's `project.yml` it
overrides the global choice for that project's tasks only (each
container gets its own supervisor, so mixed modes coexist on one
host).  A project pinning `services: {mode: socket}` still needs the
policy even after a global switch to `tcp`.

The TCP transport is **not** deprecated — it's a supported opt-out.
Caveat: the per-container ports are visible on the host via `ss -tlnp`
(127.0.0.1 only).  They don't leak off-loopback, but on multi-user
hosts another user could see that *something* is listening.

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
