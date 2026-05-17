# Container Runtimes

terok supports two OCI runtimes:

- **crun** (default) — OS-level containers via crun.
- **krun** (experimental) — KVM microVM isolation via libkrun.

| | crun | krun |
|---|---|---|
| Isolation | namespaces + cgroups + SELinux | KVM hypervisor |
| Status in terok | stable, default | **experimental** |
| Needs KVM (`/dev/kvm`) | no | yes |

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

## krun runner behaviour

- The container's PID 1 runs as `root`.
- `sudo` is not available inside the guest.
- `run.nested_containers: true` is rejected at launch.

## Login

`terok login <project> <task>` works for both runtimes.  Under crun it uses `podman exec`.  Under krun it uses `ssh` to an in-container sshd reached via a per-task loopback port forward.  Two users can log in with the same key:

- `dev` — default; use this for agents that refuse uid 0.
- `root` — log in as root.

Under krun, each running task holds one loopback TCP port for sshd, visible to every local user on the box.

## Image build

One L0/L1/L2 chain serves both runtimes — toggling `run.runtime` doesn't trigger a rebuild.
