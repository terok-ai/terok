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
| **down** | Egress allowed except private ranges and denied hosts (`down --disengage` lifts even those — the **disengaged** posture enforces nothing); audit logging still active | High — see [Shield Down](#shield-down) |
| **disabled** / missing | No firewall hooks installed at all; no audit logging | Highest — see [Shield Disabled](#shield-disabled-or-missing) |

---

## Shield Down

When the shield is **down** — whether via `terok shield down`, the TUI
toggle, or the `shield.down_on_task_run` config — the nftables rules
switch to allow, but the OCI hook infrastructure remains in place and the
private-range (RFC 1918) reject and the explicit deny sets stay unless you
pass `--disengage` — the **disengaged** posture enforces nothing at all.
A private host or range can be opened in any posture with a `shield.override`
entry (a CIDR is accepted but logged as a warning); the private-range reject
sits above the allow tiers, so a plain `shield.allow` entry does not open it
— `--disengage` opens everything without an override.  **Audit logging
continues.**

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

**Internal network exposure** (with `down --disengage`, with the shield
disabled, or through a private `shield.override` entry).  Containers
without egress filtering can reach hosts on private networks (RFC 1918
ranges: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).  On a host
connected to a corporate LAN, VPN, or cloud VPC, that exposes internal
services to the agent.  Plain `shield down` keeps the private-range
reject in place; a `shield.override` entry opens only the host or range
it names.

### What you keep

- **Audit logging** — connection attempts are still logged to
  `{task_dir}/shield/audit.jsonl`, and so is every connection the shield
  accepted *only because it is down* — one UP would refuse.  The task's
  [harvest](#reading-back-what-a-task-needed) still says what it needed, so
  a task can run DOWN and have its needs promoted into policy before you
  raise the shield.
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
    start a new task.  The verbs that would widen a task's egress —
    `terok shield allow`, `bypass --for`, `override` and
    `terok shield promote --to task` — are refused while the switch is set,
    rather than reporting a grant no ruleset took.

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

## Egress Sets

Named bundles of hosts feed the project-allow tier so common workflows keep
working while the shield is up.  Two kinds share one namespace in
`shield.sets`:

- **Built-in sets** ship with terok: git hosting, language package
  registries (`python`, `node`, `rust`, `go`), container registries, and
  `os-packages` — the distro package repos, resolved automatically from the
  project image's package family (`dnf` vs `apt`).
- **Your own sets** are plain files, one per set, in
  `~/.config/terok/egress-sets/` (`$XDG_CONFIG_HOME/terok/egress-sets/`
  when that is set).  One host per line — a domain name, an IP address or
  a CIDR — and `#` starts a comment:

  ```text
  # ~/.config/terok/egress-sets/vendor-mirrors.txt
  artifacts.vendor.example   # build cache
  pypi-mirror.vendor.example
  203.0.113.0/24             # the vendor's download range
  ```

  The file name is the set's name — lowercase letters, digits and `-`, so
  this one is `vendor-mirrors`.  No wildcards (`*.vendor.example` cannot be
  resolved on a DNS tier without live dnsmasq; list each host), no ports,
  and not `localhost`.  A file named after a built-in set, `recommended` or
  `none` is ignored rather than shadowing anything, and `terok shield sets`
  says so.

  A set reaches only **public** destinations.  Its entries are project
  allows, and the shield refuses every private address (RFC 1918, and IPv6
  ULA) in the security tier, above any set — so a private range in a set
  file, or an internal mirror whose name resolves to a private address,
  is still refused.  Nothing warns you: the set loads and lists as usual,
  and the refusals show in the task's harvest as `private_range`.  Reach a
  private host with a
  [`shield.override`](#per-project-allow-and-break-glass-override) instead,
  with its reason and expiry.

Run `terok shield sets` to list both kinds: the built-in sets with their
hosts, and your own with their files and the projects that name them —
the projects an edit to a set's file widens, from their tasks' next start.

No set applies unless the project selects it in `shield.sets` in
`project.yml`; without a selection, the project-allow tier holds only the
git remote host and the project's own `shield.allow` entries.
`recommended` names every **built-in** set, including sets added in future
releases, and the new-project wizard offers it.  It never includes your own
sets — a set written for one project would otherwise widen every
recommended project — so name each of yours.  The TUI project screen's
*Egress sets* picker (your sets have their own group, outside
*Recommended*) and `terok shield sets <project> --set …` write the
selection:

```yaml
shield:
  sets: [recommended]                          # every built-in set, including future ones
  # sets: [recommended, vendor-mirrors]        # … and one of your own
  # sets: [git-hosting, python, os-packages]   # freeze this exact selection
  # sets: []                                   # explicitly no sets, as when unset
```

A project that names one of your sets whose file is missing, or does not
parse, **fails to load** with an error naming the set and its file.  Fix the
file or the selection: terok does not guess which hosts you meant.

terok writes the selection into `project.yml` in place: only the lines the
change needs are written, and every other line — comments, `[flow]` lists,
indentation, how you spelled a value, the line ends — stays byte for byte,
as do the file's permissions.  A write-protected `project.yml` is left
alone, and so is one where the change could not be placed without rewriting
lines around it; either way the error names the change, for you to make by
hand.

Two caveats worth knowing:

- Set entries are ordinary t40 allows — a security-deny and the
  private-range reject always win over them, and on the `dnsmasq-live` DNS
  tier a listed domain admits its subdomains by suffix match.
- Hostname allowlists cannot cover community *mirror pools* (Fedora's
  metalink hands dnf arbitrary mirror hosts).  The `os-packages` set
  covers the distros' own hosts (metalink + primary download); a blocked
  community mirror surfaces in the audit log and dnf falls back through
  its mirror list.

## DNS Tiers

Shield needs a resolver on the host to turn allowlisted domains into
addresses.  The one it finds decides what the firewall can do:

| | `dnsmasq-live` | `dnsmasq-static` | `lookup` | `getent` |
|---|---|---|---|---|
| Allow sets follow IP rotation; `*.example.com` entries accepted | yes | no | no | no |
| Blocked connections show the domain, not only the address | yes | yes | no | no |
| Host needs | dnsmasq built with nftset support | any dnsmasq | `dig` or `drill` | glibc only |

Install dnsmasq (`dnsmasq-base` on Debian and Ubuntu) for the live tier.
A degraded tier is named at launch (on stderr), in the task's details
(`DNS:`), and as a warning in `terok sickbay`.

What a degraded tier costs is **pinning**: the names the shield resolves
itself are enforced by the addresses they resolved to, and a provider can
move a name to another address at any time.  Three kinds of name are
pinned, each with its own consequence:

- **Allowlisted names** (the git upstream host, the egress sets,
  `shield.allow`, the provider endpoints, runtime allows) on every tier but
  `dnsmasq-live`: while the shield is up, an address one of them rotates to
  is refused.
- **Denied names** — the vault-relayed provider hosts — on `lookup` and
  `getent`, where no dnsmasq sinkholes them: an address one of them rotates
  to *escapes the deny* when the shield is down, a bypass window is open,
  or an allowlisted name shares it.  This one is a security gap, and is
  worded as one.
- **Break-glass override names** on *every* tier, `dnsmasq-live` included —
  overrides never follow DNS — so an address one of them rotates to is not
  reached.

IP and CIDR entries cannot rotate, and `localhost` and
`host.containers.internal` never do, so none of them is listed.  The shield
lists the pinned names, worded for the posture they matter in (under DOWN
only the denied names still do; disengaged, none):

- in the task's `audit.jsonl`, as `note` events, at every launch and
  restart and at each `shield up` and `shield down` (while `shield.audit`
  is on);
- on stderr when a task is launched or restarted from the command line,
  worded for the posture it was left in, and after `terok shield up` and
  `terok shield down`;
- in the task's details, under `Pinned:`;
- in the notice after a TUI `shield up` or `shield down`.

The shield also limits pinning: on a pinning tier every `shield up` and
`shield down` re-resolves the names whose resolution is more than an hour
old before it reseeds the sets, so a posture change picks up a move.
Nothing re-resolves them in between.  The full table, and how to point
terok at a self-built dnsmasq with `shield.dnsmasq_path` in `config.yml`,
are in the
[terok-shield DNS tiers](https://github.com/terok-ai/terok-shield/blob/master/docs/guide/modes.md#dns-tiers)
page.

## Per-Project Allow and Break-Glass Override

Two additive `project.yml` layers shape a task's egress policy on top of
the egress sets:

```yaml
shield:
  allow:                # extra hosts allowed while the shield is up
    - ftp.mirror.example.org
  override:             # break-glass: reachable despite a security-deny
    - host: api.anthropic.com
      reason: direct SDK testing against the live endpoint
      expires: 2026-12-31   # valid through that day, host local time
```

- **`shield.allow`** entries join the project-allow tier alongside the
  project's git remote host and its selected egress sets.  They are
  ordinary allows: a security-deny still wins over them.
- **`shield.override`** entries sit *above* the security-deny — the only
  way to reach a host terok deliberately denies, such as a vault-relayed
  provider endpoint, or a private (RFC 1918) address.  A host, IP, or CIDR
  per entry — opening the agent to several local services is a legitimate
  way to run, but a range widens a whole subnet through the firewall, so
  shield logs it as a warning (and an `override_range` audit event) at
  launch — a mandatory `reason` for the audit trail, and an optional
  `expires` date.  For a vault-relayed host an override adds the *direct*
  route only: agents keep relaying their keys through the vault.  No
  override reaches the hard-deny floor (link-local, cloud metadata) or the
  host's own loopback, which a `+localhost:PORT` shield grant reaches one
  port at a time; shield leaves such addresses out of the tier and notes
  each one.
- **Expiry is enforced by the kernel.**  An override is valid *through* its
  `expires` date: terok hands it to shield with that date, and the t10
  element carries a timeout that runs out at the end of the day, host local
  time — on a task that keeps running past it too, with no restart needed.
  A task started after the date never gets the override; shield leaves it
  out and records an `override_expired` audit event, so a break-glass that
  lapsed while the task was stopped says why it is gone.  An entry without
  `expires` holds until you remove it.  The end of an override stops *new*
  connections: the ruleset accepts established traffic ahead of every tier,
  so a connection the override let through runs until it closes.

The policy tiers are recomputed from the current roster and project
config on every launch **and every plain restart**, so config edits reach
a stopped task the next time it starts.

> **Migration note (0.9):** terok tasks compose no shield profile.  Move
> hosts from a customized profile under `~/.config/terok/shield/profiles`
> to `shield.allow` — or, when several projects share them, into one of
> your own [egress sets](#egress-sets) (drop each line's leading `+`).  A
> vault-protected provider endpoint needs a `shield.override` entry (with
> its auditable reason and expiry), because allows compose below the
> security-deny, which always wins.

### Breaking Glass on a Running Task

To open an override on one running task now, without touching
`project.yml` — or to end one early — use the shield's own verb:

```bash
terok shield override <project> <task> api.example.org \
    --expires 2026-11-30 --reason "vendor outage"         # open it now
terok shield override <project> <task> api.example.org --off   # revoke it now
```

A grant needs both `--expires` and `--reason`; `--off` takes neither.  The
grant is kernel-timed like an authored override, and lasts until it expires
or the task's next restart, which re-derives the overrides from
`project.yml` — write it there, or let
[`terok shield promote --to override`](#promoting-what-a-task-needed) write
it, to keep it.  `--off` ends a live grant for good.  On an authored
override, `--off` ends it on that task only, and only until the task's next
restart, which restores it from `project.yml`.  Remove the entry from
`project.yml` to end it everywhere.

### Live Allows Stop at the Security Tier

`terok shield allow <project> <task> HOST` allows a host for one task: live
while the task runs, recorded in its runtime overlay for its next start
when it is stopped.  A clearance *Allow* takes the same path, and so does
[`terok shield promote --to task`](#promoting-what-a-task-needed).  None of
them lifts the security tier.  A target that tier refuses — a vault-relayed
provider host, a name under one of its domains, or anything that resolves to
one of its addresses — is refused before anything changes, nothing granted:

```text
Error: cdn.example.net shares the address 203.0.113.7 with the security tier — break-glass only: use a shield.override (reason + expires) to reach it directly
```

The message names the shared address when that is the cause, the refusal is
audited as `allow_refused`, the command exits non-zero, and a clearance
popup shows the same line.  On the `lookup` and `getent` DNS tiers, where
nothing sinkholes the vault hosts, the check resolves their names afresh
first, so an address a vault host has moved to is caught as well.  What a
live allow *may* lift is your own runtime deny — `terok shield deny`, or a
clearance *Deny* — which it flips.

---

## The Bypass Window — a Timed Allow-All

A task sometimes needs a host the policy does not name yet, and reaching for
`terok shield down` to get it tends to become permanent.  The bypass window is
the bounded alternative:

```bash
terok shield bypass <project> <task>               # how long is left
terok shield bypass <project> <task> --for 5m      # open it
terok shield bypass <project> <task> --off         # close it now
```

In the TUI it is `b` on the task's action list, which states what the window
does before asking how long to open it for; an empty duration closes it.  A
notice confirms the window opened (for how long) or closed — or says why it
could not.  While a window is open, the task's details say so — `Bypass:
open until 14:32:10 (4m 59s left)`, with the shield's `up` drawn in the
warning colour rather than as the trusted state — and count down from the
kernel's own timeout, read once; when it runs out, the shield is asked again
to confirm the window closed.

While the window is open, what the allowlist would refuse is accepted **and
logged** — not everything:

- The window sits *below* the security tier.  Vault-relayed provider hosts
  (and anything sharing their addresses), the private ranges and the hard-deny
  floor (link-local, cloud metadata) stay refused; reaching any of them takes
  a `shield.override`, with its reason and expiry.  A window is not a
  disengage.
- It opens only while the shield is **up** — the UP posture's chain is the
  only one that reads it.  Under `shield down`, which accepts by default
  anyway, opening one is refused with the posture named.
- Every connection it admits is recorded in the task's `audit.jsonl` as
  `bypass`, one line per target per 30 seconds, so what the task actually
  needed can be read back afterwards — and moved into `shield.sets`,
  `shield.allow` or a `shield.override` where it belongs, which
  [`terok shield promote`](#promoting-what-a-task-needed) does for you.

The window lives in the kernel, as an nftables element carrying a timeout.  It
closes itself when the time runs out, and a `shield up`, a task restart, or a
host process that dies closes it **sooner** — there is no host-side deadline to
outlive the shield, and nothing renews it.  `shield.bypass_duration` in
`config.yml` (default `5m`) sets the duration used when none is named:

```yaml
shield:
  bypass_duration: 5m   # an nft timeout: a count and one unit (30s, 5m, 2h)
```

---

## Muting the Prompts, Not the Shield

A task that keeps knocking on the same closed door produces the same
Allow/Deny prompt over and over, and an operator who cannot silence it will
silence the shield instead — which is the outcome this whole design exists to
avoid.  So the prompts can be muted per task:

- In the clearance screen, `m` mutes the task the highlighted request came
  from — one key, at the moment the noise is in front of you.
- In a task's action list, `m` mutes and `M` un-mutes.  They are separate
  verbs rather than a toggle: the mute lives in the hub's memory, which every
  start of the container rebuilds in the project's mode (below), so saying
  which state you want always lands.
- On the command line, `terok task mute <project> <task>` and
  `terok task unmute <project> <task>` do the same, and exit non-zero when
  the task's hub could not be told.

Either way a notice says what happened — or that the task's hub did not
answer, in which case nothing is claimed — and warns when the task's shield
audit is off: a muted task with audit off is refused things that are now
neither prompted nor recorded.

A running task's details say who is asked: `Prompts: asking`, `muted`,
`clearance off` (it started with `services.clearance: off`, below), or
`unknown` when its hub does not answer.  The state is read back from the
task's own container, never from the config: from the hub itself — the only
place a mute lives — after every `m` / `M`, never assumed from what was
asked for; and, when no hub answers, from the mode the container started
under.

What muting does **not** do:

- It does not allow anything.  The connection was refused in the kernel
  before the clearance hub ever saw it, and later ones are refused the same
  way.
- It does not deny anything either.  A mute is not a verdict; nothing becomes
  sticky.
- It does not hide anything.  Every refusal is still written to the task's
  `audit.jsonl` (while shield audit is on, as it is by default), so a muted
  task is silent, not invisible.  Lifecycle events — shield down, container
  exited — are never muted.

The mute lives in that container's clearance hub, so it lasts until the task
next starts: every start builds a new hub in the project's mode, audible
under `prompt` and muted under `mute`.

### Who is asked, per project: `services.clearance`

Who is asked about a refusal is a setting — global in `config.yml`, and per
project in `project.yml`, where it overrides the global one:

```yaml
services:
  clearance: prompt   # the default: each refusal is put to the operator, live
  # clearance: mute   # nobody is asked: tasks start muted (M unmutes a running one)
  # clearance: off    # no clearance hub or verdict server at all
```

`mute` and `off` change who is asked, never what the firewall does: refusals
stay enforced either way, and the audit log is then a refusal's only record
(while `shield.audit` is on — with audit off, nothing records it).  `off` also
removes the verdict path, so no prompt can ever widen the firewall, and the
task runs two fewer supervisor children; `m` / `M` on a task started that way
say so.  With `off` globally, `off` for the selected project too, and no task
running a hub, the TUI's live clearance screen (`C`) says so instead of
opening on nothing.

A change applies at the task's next start: creation, re-creation, a plain
restart or a headless follow-up, which rewrite the setting into the
container's sidecar before starting it.  Until then a running task keeps the
mode it started with — its hub still prompts after a switch to `off`, and
`m` / `M` and its details go by that hub, not by the new setting.  A runtime
`m` / `M` lasts until the next start — which returns the task to its
project's mode — and a bare `podman start` replays whatever the sidecar last
recorded.  An unknown value in `config.yml` warns and reads as
`prompt`; an unknown value in `project.yml` fails the project's load and
names the key, for you to fix.

### How long a prompt waits: `clearance.verdict_ttl`

A prompt that nobody answers expires after 24 hours.  The global
`config.yml` sets another lifetime; `project.yml` has no such key:

```yaml
clearance:
  verdict_ttl: 2h        # seconds, or a count with s, m, h or d
  # verdict_ttl: never   # a prompt waits until its container ends
```

An expired prompt can no longer be answered.  The connection was refused
when it was made, and it stays refused.  Like the mode, a changed lifetime
applies at the task's next start.

---

## Reading Back What a Task Needed

A deny-by-default box is only workable if you can find out what the policy
refused and decide whether it should have.  That record already exists, in
every posture: the shield writes into the task's `audit.jsonl` one line per
refusal, one per accept through a bypass window, and — while the shield is
down — one per connection accepted *only because it is down*, which UP would
refuse.  So a task running DOWN, the default, leaves a harvest as useful as
one running UP.  `harvest` reads it back:

```bash
terok shield harvest <project> <task>          # what can be promoted first, the floor last
terok shield harvest <project> <task> --json   # the same entries, machine-readable
```

Each entry says what the shield did — its *action* — and which tier stands
between the task and the target:

| Action | Recorded when |
|---|---|
| `blocked` | nothing allowed it, so UP refused it — or quarantine dropped it |
| `bypass` | a bypass window let it through (UP) |
| `down` | the shield accepted it only because it was down (DOWN, disengaged) |
| `security_deny` | the security tier refused it (UP, DOWN) |
| `private_range` | a private range, or the hard-deny floor, refused it (UP, DOWN) |

The tier is read from the *destination*, not from the action: a disengaged
shield refuses nothing, so its `down` lines can name a vault-relayed host,
cloud metadata or a LAN address — and each lands in the tier that will refuse
it once the shield is back up, with only the remedies that tier allows.  A
harvest needs `shield.audit` on (the default) when the task was created;
with audit off nothing is recorded, and the harvest says so rather than
passing for an all-clear.

In the TUI it is `h` on the task's action list — *harvest and promote*, the
review and [`promote`](#promoting-what-a-task-needed) on one screen.  The
targets are grouped the way the decision is made: suggested egress sets
first; then what nothing allows yet — refused by the policy, let through by a
bypass window, or accepted only because the shield is down (which UP would
refuse); then your own runtime denies; the security tier and the private
ranges, break-glass only; the hard-deny floor last.  A target the shield
classifies under a tier this terok does not know gets a heading of its own —
nothing is left out.  The pane under the list says, for the highlighted
target, which destinations its tier allows and why each other one is closed.

| Key | Does |
|---|---|
| `enter` | promote the highlighted target — or, on a suggested set, enable it for everything it covers |
| `t` / `p` / `s` / `o` | promote it to this task / the project / an egress set / a break-glass override |
| `r` | read the harvest again |
| `q` / `Esc` | back |

A key whose destination the tier closes says why instead of opening
anything.  The promotion form lists the four destinations with the closed
ones disabled and the reason for each under them, asks for an egress set's
name (suggesting the sets that cover the target, then your own) or for a
break-glass override's reason and expiry — both required, the expiry today
or later — and has a break-glass confirmed once more before it writes
anything.  A refusal stays in the form, next to what to fix; a promotion's
result is announced with when it takes effect — live on the task now, or at
its next start — and the review reloads on the same target.  When nothing was
harvested, the screen says what that means: with audit off, nothing could
be; with it on, what the shield's current posture records (under DOWN, what
UP would refuse is accepted, and recorded as accepted only because the
shield is down).

Entries are keyed by **target** — the domain when the shield recovered one from
DNS, the address otherwise — because an allowlist entry for a rotating CDN is
worth nothing as an IP.  The count is a floor on attempts rather than a
connection count: at most one line is recorded per target per 30 seconds.

`harvest` changes nothing.  Acting on what it shows stays an explicit act —
[`promote`](#promoting-what-a-task-needed) — and the tier that refused a host
still decides what is possible: a host denied by the security tier can never be
promoted into the project allowlist — it takes a `shield.override`, with its
reason and expiry, or nothing.

---

## Promoting What a Task Needed

`terok shield promote` turns a task's harvest into policy, one explicit grant at
a time.  Without `--to` it reviews the harvest:

```bash
terok shield promote <project> <task>                   # the review, with commands
terok shield promote <project> <task> --target pypi.org # one target only
terok shield promote <project> <task> --json            # the same, machine-readable
```

The review merges what the shield recorded per target, groups the targets by
the tier standing in their way, and prints under each the commands that would
promote it — the destinations that tier allows, and no others:

| Tier | Standing in the way | Promote to |
|---|---|---|
| `t40` | nothing allows it — refused, let through by a bypass window, or accepted only because the shield was down | `--to task`, `--to project`, `--to set:NAME` |
| `t20_operator` | your own runtime deny (`terok shield deny`, a clearance *Deny*) | `--to task`, `--to project` |
| `t20_security` | the security tier — vault-relayed provider hosts, and anything sharing their addresses | `--to override`: break-glass, with a reason and an expiry the kernel enforces |
| `t20_private` | a private network range (RFC 1918, ULA) | `--to override`: break-glass, with a reason and an expiry |
| `t00` | the hard-deny floor (link-local, cloud metadata) | nothing, ever |
| `t00` | the host's own loopback (`host.containers.internal`) | nothing: only a per-port `+localhost:PORT` shield grant reaches it |

The tier is the one the review, `--json` and the
[export](#sending-a-harvest-back) name.  A target seen under more than one —
a name with several addresses — takes the most restrictive, with that tier's
destinations: a remedy has to work for every address the target was seen at.

The destinations:

- **`--to task`** allows the target for this task alone: live while it runs,
  and — when it is stopped — recorded in the task's runtime overlay for its
  next start.  The grant stays with that task across restarts.
- **`--to project`** adds the target to `shield.allow` (nothing is written when
  the git upstream host, an enabled set or `shield.allow` already grants it).
- **`--to set:NAME`** enables a built-in egress set that names the target —
  built-in sets are read-only, so one that does not name it is refused — or
  adds the target to one of [your own sets](#egress-sets), creating the file
  when it is new, and enables that set.  The result names the other projects
  whose tasks the edit widens.  `--to set:NAME` *without* `--target` enables
  the set for everything it covers in the harvest; the review suggests these.
- **`--to override`** writes a break-glass `shield.override` and needs both
  `--reason TEXT` and `--expires YYYY-MM-DD`; no other destination takes
  either.  The override is valid through that day: the kernel closes it at
  the end of it, host local time, on a task still running then too.  For a
  vault-relayed host it adds the direct route only: agents keep relaying
  their keys through the vault.  An override is refused for a target nothing
  but the allowlist refuses — lifting a host above the security tier for no
  reason is no promotion.

A project, set or override promotion applies to every task of the project from
its next start, and also reaches the task it came from: live while that task
runs (a break-glass through the kernel-timed t10 set, until its expiry), so you
see the effect at once; a stopped one gets it at its next start, like every
other task.  Other running tasks of the project are left alone until they
restart.  A target held by the task's own runtime deny has that
deny lifted too — live, or for the task's next start — since no project grant
reaches past it.  Every result says what took effect now and what waits for a
next start.

The review also **suggests egress sets**: a greedy cover of the targets nothing
allows yet, by the sets the project does not enable — the set covering the most
first, ties to the smaller set, then built-in before your own.  A set covers a
target by its exact name, and on the `dnsmasq-live` DNS tier also by suffix,
since a listed domain admits its subdomains there.  On the `lookup` and
`getent` tiers the shield records addresses only, so there is nothing to match:
the review says *no domain attribution on this tier* and suggests nothing, and
an address you promote stops matching when its name moves.  Run a dnsmasq tier
when you harvest to improve your sets.

A target must come from the task's harvest — `promote` is a review of evidence,
not a general allowlist editor; `terok shield sets` and `project.yml` remain
the places to edit policy by hand.

---

## Sending a Harvest Back

User groups that run terok can send back what their tasks needed, so the
built-in sets improve for everyone.  `terok shield export` writes it:

```bash
terok shield export <project>                          # every task that ran shielded
terok shield export <project> --task <task>            # one task
terok shield export <project> --scrub --output harvest.json
```

The output is one JSON document, aggregated across the project's tasks:

```json
{
  "format": "terok-egress-export",
  "version": 1,
  "generator": "terok 0.9.3",
  "generated_at": "2026-10-27T10:00:00+00:00",
  "scrubbed": false,
  "project": "myproj",
  "tasks": ["k3v7a", "m2x9q"],
  "task_count": 2,
  "audit_off_tasks": 0,
  "dns_tiers": ["dnsmasq-live"],
  "domain_attribution": true,
  "enabled_sets": ["git-hosting"],
  "entries": [
    {
      "target": "pypi.org",
      "tier": "t40",
      "tiers": ["t40"],
      "remedies": ["task", "project", "set"],
      "actions": ["blocked", "bypass"],
      "count": 7,
      "tasks": 2,
      "first_seen": "2026-10-20T09:14:02+00:00",
      "last_seen": "2026-10-26T17:40:51+00:00",
      "ports": [443],
      "addresses": ["203.0.113.10"]
    }
  ],
  "suggestions": [
    {"set": "python", "origin": "built-in", "covers": ["pypi.org"], "of": 1, "hosts": 2}
  ]
}
```

- `format` and `version` identify the document; the version is bumped when a
  field changes meaning or goes away.
- Each entry is one target, merged across tasks: `count` sums the attempts (a
  floor — the shield records at most one line per target per 30 seconds),
  `tasks` says how many tasks saw it, `ports` and `addresses` are joined, and
  `first_seen`/`last_seen` span them all.  `tier` is the most restrictive
  standing the target was seen under and `tiers` every one of them; `remedies`
  are what could lift `tier` — the vocabulary of the table above.
- `audit_off_tasks` counts tasks created with shield audit off: their harvest
  is empty by construction, which is no all-clear.
- `domain_attribution` is `false` when no task ran a DNS tier that names what
  it refuses; `suggestions` is then empty.

`--scrub` makes the document safe to share outside your network: address
targets and every `addresses` list go, as do names that only mean something
inside one network — single-label names, `.local`, `.lan`, `.internal`,
`.home.arpa` and `.corp` names, names refused as private or link-local
destinations, and names seen only at addresses that are not globally
routable.  The project's name and its task ids go too, and so do the names of
your own sets: `enabled_sets` and `suggestions` keep built-in sets only, which
mean the same on every host.  `scrubbed_out` counts what was dropped, by
reason (`addresses`, `internal_names`).

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
