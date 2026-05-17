# Container Runtimes

terok supports two OCI runtimes:

- **crun** (default) — conventional OS-level containers via crun.  Mature, fast startup, no extra deps beyond podman itself.
- **krun** (experimental) — KVM microVM isolation via libkrun.  Each task runs in its own tiny VM; the host kernel is the security boundary, not just namespaces.

## When to use which

| | crun | krun |
|---|---|---|
| Isolation | namespaces + cgroups + SELinux | KVM hypervisor |
| Startup | <1 s | a few seconds |
| Status in terok | stable, default | **experimental** |
| Needs KVM (`/dev/kvm`) | no | yes |

Pick crun unless you specifically want hardware-mediated isolation.  krun's blast-radius story is stronger, but it ships behind an opt-in flag while the integration matures.

## Optional dependencies for krun

| Package | Purpose | Install (Fedora) |
|---|---|---|
| `crun-krun` | Adds libkrun and points podman's `--runtime krun` at the right binary | `dnf install crun-krun` (or `rpm-ostree install` on Silverblue, then reboot) |

User must be in the `kvm` group to read `/dev/kvm`.  Verify with `ls -l /dev/kvm` and `groups`.

## Enabling krun

Two steps:

**1.** Flip the global flag in `~/.config/terok/config.yml`:

```yaml
experimental: true
```

**2.** Set the runtime — either globally in `~/.config/terok/config.yml`:

```yaml
run:
  runtime: krun
```

…or per project in `project.yml`:

```yaml
run:
  runtime: krun
  krun_cpus: 4         # optional, microVM vCPU count
  krun_ram_mib: 4096   # optional, guest RAM in MiB
```

Project-level settings override the global default.  Without `experimental: true` set globally, any krun selection fails fast at startup with a pointer at the opt-in — a typo in `project.yml` can't silently switch isolation backends.

## Switching runtime via env var

For one-off testing without editing any file:

```bash
TEROK_RUNTIME=krun terok task start <project>
```

Accepted values: `crun` (default), `krun`, `null` (in-memory stub for CI).

## How terok reaches into a krun guest

`podman exec` can't enter a krun microVM (libkrun can't inject processes post-boot — see [crun#1098](https://github.com/containers/crun/issues/1098)), so terok runs a hardened sshd inside the guest and reaches it from the host over a per-task host TCP port that podman's passt has forwarded into the guest namespace.

At task launch under krun, terok:

1. Reserves a free loopback TCP port on the host.
2. Adds `-p <host_port>:22` to the `podman run` invocation so passt forwards it into the guest's sshd:22.
3. Bind-mounts the live host SSH public key onto `/etc/ssh/authorized_keys.d/terok` inside the guest.
4. Records the host port in a `terok.krun.port` annotation so `terok exec` / `terok login` can find it again.

The guest's sshd is gated by `ConditionFileNotEmpty=/etc/ssh/authorized_keys.d/terok` — under crun the file ships empty and the service stays dormant; under krun the bind-mount makes it non-empty and the service starts on TCP 22.  One L0 image serves both runtimes, no per-installation secret baked in.

## Operational notes

- **Login.**  `terok login <project> <task>` works for both backends — under krun it prints an `ssh -tt -p <host_port> -i <key> … dev@127.0.0.1` invocation instead of `podman exec -it …`.  The shortcut command is the same.
- **Host-visible ports.**  Each running krun task holds one loopback TCP port for sshd.  Visible to every local user on the box — acceptable while krun is experimental; revisit if/when the runtime stabilises.
- **`run.nested_containers: true` is incompatible with krun.**  The microVM image doesn't ship a nested-container stack; terok refuses the combination at task launch.
- **Image build.**  A single L0/L1/L2 image chain serves both runtimes — the L0 layer ships sshd dormant under crun, active under krun.  No separate krun image, no rebuild churn when toggling a project's runtime.
- **Host SSH key.**  terok mints a `%host`-scope keypair in the vault on first use and bind-mounts the public half into `/etc/ssh/authorized_keys.d/terok` at task launch.  Rotating the vault key means tasks launched after the rotation use the new key; in-flight tasks keep working until they exit.
