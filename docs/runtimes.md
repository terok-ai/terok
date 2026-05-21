<!--
SPDX-FileCopyrightText: 2026 Jiri Vyskocil
SPDX-License-Identifier: Apache-2.0
-->

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

Check `ls -l /dev/kvm` — if it's world-rw (`crw-rw-rw-`, the Fedora default) you're done; if it's `crw-rw---- root:kvm` (Debian/Ubuntu default), add your user to the owning group.

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
  memory: 4g     # optional, podman --memory format
  cpus: 4        # optional, podman --cpus format
```

Project-level settings override the global default.  Without `experimental: true` set globally, any krun selection fails fast at startup with a pointer at the opt-in — a typo in `project.yml` can't silently switch isolation backends.

## Resource limits

`run.memory` and `run.cpus` apply to both runtimes.

Under **crun** they ride on podman's `--memory` / `--cpus` flags and become cgroup quotas: the scheduler and OOM killer enforce them, but `btop` / `nproc` / `/proc/meminfo` inside the container still report host values (check `/sys/fs/cgroup/{cpu,memory}.max` to verify).

Under **krun** the asymmetry surfaces:

- `run.memory` works through podman's `--memory` because [crun-krun](https://github.com/containers/crun/blob/main/krun.1.md) reads the OCI memory limit as a fallback when sizing the microVM, so `free` reports the right number.
- `run.cpus` does *not* size the VM by itself — `--cpus` only sets the cgroup CFS quota, which throttles the VM host-side but doesn't change its vCPU count.  terok additionally emits the `krun.cpus` OCI annotation (the documented knob crun-krun reads, rounded up from `run.cpus` and capped at 16), so the guest sees the matching vCPU count.

## Switching runtime via env var

For one-off testing without editing any file:

```bash
TEROK_RUNTIME=krun terok task start <project>
```

Accepted values: `crun`, `krun`, `null` (in-memory stub for tests).
Unset means "fall through to project/global config".

## krun runner behaviour

The container's PID 1 runs as `root`, and `run.nested_containers: true` is rejected at launch.

## Login

`terok login <project> <task>` works for both runtimes.  Under crun it uses `podman exec`.  Under krun it uses `ssh` to an in-container sshd reached via a per-task loopback port forward.  Two users can log in with the same key:

- `dev` — default; use this for agents that refuse uid 0.
- `root` — log in as root.

Under krun, each running task holds one loopback TCP port for sshd, visible to every local user on the box.

## Image build

One L0/L1/L2 chain serves both runtimes — toggling `run.runtime` doesn't trigger a rebuild.
