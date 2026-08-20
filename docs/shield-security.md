# Shield Security Guide

> [!WARNING]
> This documentation was written by an AI agent and might be inaccurate.

The **shield** is an egress firewall that restricts outbound network access
from task containers using nftables OCI hooks.  It is provided by
[terok-shield](https://github.com/terok-ai/terok-shield) and integrated
into terok through the
[terok-sandbox](https://github.com/terok-ai/terok-sandbox) runtime layer.
This page explains **what you lose** when the shield is weakened or absent.

---

## Shield States

| State | Meaning | Risk |
|-------|---------|------|
| **up** (deny-all) | Only allowlisted IPs/domains and the gate server are reachable | Low — intended production state |
| **down** | Egress allowed except private ranges (`down --disengage` lifts even that — the **disengaged** posture); audit logging still active | High — see [Shield Down](#shield-down) |
| **disabled** / missing | No firewall hooks installed at all; no audit logging | Highest — see [Shield Disabled](#shield-disabled-or-missing) |

---

## Shield Down

When the shield is **down** — whether via `terok shield down`, the TUI
toggle, or the `shield.down_on_task_run` config — the nftables rules
switch to allow, but the OCI hook infrastructure remains in place and the
private-range (RFC 1918) reject stays unless you pass `--disengage`.
**Audit logging continues.**

### What you lose protection against

**Secrets exfiltration.**
A compromised or prompt-injected agent can send API keys, tokens, SSH
private keys, or any other secrets mounted in the container to arbitrary
external endpoints.  With the shield *up*, outbound connections are
limited to the configured allowlist, narrowing the destinations an
exfiltration attempt can reach.

**Prompt injection surface.**
Without egress restrictions, the agent can fetch content from any host on
the internet, including attacker-controlled pages.  The shield narrows
this to the allowlist; it does not stop injection from content served by
allowlisted hosts.

**Internal network exposure** (only with `down --disengage` or the shield
disabled).  Containers without egress filtering can reach hosts on
private networks (RFC 1918 ranges: `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`).  On a host connected to a corporate LAN, VPN, or cloud
VPC, that exposes internal services to the agent.  Plain `shield down`
keeps the private-range reject in place.

### What you keep

- **Audit logging** — connection attempts are still logged to
  `{task_dir}/shield/audit.jsonl`.
- **OCI hook infrastructure** — the shield can be raised again at any time
  via `terok shield up` or the TUI.
- **Gate server** — the git gate still directs agent pushes to the
  host-side mirror for human review (in gatekeeping mode), though it
  does not prevent outbound network connections on its own.

---

## Shield Disabled or Missing

When the shield is **disabled** via the
`shield.disable_firewall_no_protection` global config option, or when
terok-shield is not installed or cannot run (e.g. because `nft` is missing
or the podman version is incompatible), **no OCI hooks are installed at
all**.  This is the most dangerous state.

!!! danger "You lose everything listed above, plus:"

    **No audit logging.**
    Without the OCI hook, no connection data is recorded.  You have zero
    visibility into what the container accessed on the network.  Post-incident
    forensics become significantly harder.

    **No ability to raise the shield.**
    The `terok shield up` command and TUI toggle have no effect — there
    are no nftables rules to activate.  The only way to restore protection is
    to remove the kill-switch config, fix the underlying podman/nft issue, and
    start a new task.

### When is this acceptable?

The `disable_firewall_no_protection` option exists **only** as a transitional
escape hatch for users whose podman version is incompatible with the
OCI-hook-based shield.  It will be removed once terok-shield supports all
target podman versions.

Set it only if:

- Your podman version does not support `--hooks-dir` reliably
  (see [terok-shield#71](https://github.com/terok-ai/terok-shield/issues/71),
  [terok-shield#101](https://github.com/terok-ai/terok-shield/issues/101))
- You understand and accept the risks above
- You are not working with sensitive credentials or private networks

```yaml
# ~/.config/terok/config.yml — DANGEROUS, remove ASAP
shield:
  disable_firewall_no_protection: true
```

---

## Curated Egress Sets

Named bundles of well-known development endpoints feed the project-allow
tier so common workflows keep working while the shield is up: git
hosting, language package registries (`python`, `node`, `rust`, `go`),
container registries, and `os-packages` — the distro package repos,
resolved automatically from the project image's package family
(`dnf` vs `apt`).  Run `terok shield sets` to list them.

The default is **generous**: every curated set.  Narrow a project via
`shield.sets` in `project.yml` (the TUI project screen's *Egress sets*
picker or `terok shield sets <project> --set …` write it):

```yaml
shield:
  sets: [git-hosting, python, os-packages]   # freeze this exact selection
  # sets: []                                 # disable all curated content
  # (unset/null: the generous default — every set, including future ones)
```

Two caveats worth knowing:

- Set entries are ordinary t40 allows — a security-deny always wins over
  them, and on the dnsmasq DNS tier a listed domain admits its
  subdomains by suffix match.
- Hostname allowlists cannot cover community *mirror pools* (Fedora's
  metalink hands dnf arbitrary mirror hosts).  The `os-packages` set
  covers the distros' own hosts (metalink + primary download); a blocked
  community mirror surfaces in the audit log and dnf falls back through
  its mirror list.

## Per-Project Allow and Break-Glass Override

Two additive `project.yml` layers shape a task's egress policy on top of
the curated sets:

```yaml
shield:
  allow:                # extra hosts allowed while the shield is up
    - ftp.mirror.example.org
  override:             # break-glass: reachable despite a security-deny
    - host: api.anthropic.com
      reason: direct SDK testing against the live endpoint
      expires: 2026-08-15
```

- **`shield.allow`** entries join the project-allow tier alongside the
  project's git remote host and the configured allow profiles.  They are
  ordinary allows: a security-deny still wins over them.
- **`shield.override`** entries sit *above* the security-deny — the only
  way to reach a host terok deliberately denies, such as a vault-relayed
  provider endpoint.  One host or IP per entry (never a CIDR), a
  mandatory `reason` for the audit trail, and an optional `expires` date.
- **Expiry is evaluated when the container is launched or restarted.**
  An already-running container keeps its overrides until its next start —
  restart the task to make an elapsed `expires` take effect.

The policy tiers are recomputed from the current roster and project
config on every launch **and every plain restart**, so config edits reach
a stopped task the next time it starts.

> **Migration note (0.9):** adding a vault-protected provider endpoint to
> a custom allowlist profile no longer re-enables direct access — profile
> entries compose below the security-deny, which always wins.  Use a
> `shield.override` entry (with its auditable reason and expiry) instead.

---

## Mitigations When Shield is Down or Missing

If you must operate without the shield, consider these compensating controls:

1. **Use gatekeeping mode** — even without the shield, the git gate
   directs agent pushes to the host-side mirror instead of upstream.
   This is a configuration default, not a hard barrier — see
   [Security Modes](git-gate-and-security-modes.md) for details.
2. **Protect credentials** — SSH keys are served via the vault SSH signer
   (never mounted). Avoid placing raw API tokens in shared config dirs.
3. **Monitor container traffic externally** — use host-level firewall
   rules or network monitoring tools.
4. **Limit task duration** — shorter tasks reduce the window of exposure.
5. **Review agent output carefully** — check for unexpected network
   activity in the task logs.

---

## Related

- [Security Modes](git-gate-and-security-modes.md) — git gate and
  online/gatekeeping modes
- [Container Layers](container-layers.md) — how containers are built
- [terok-shield](https://github.com/terok-ai/terok-shield) — the egress
  firewall library
