# Changelog
## [0.9.0] — Past Prologue — 2026-09-11

### Added

- DNS tiers: terok shows a degraded tier at launch, in the task details and in `terok sickbay`. `shield.dnsmasq_path` selects the dnsmasq binary.
- Vault: set the passphrase on a new install, change it, keep it in the kernel keyring, and re-encrypt a locked database.
- Git gate: a safe-sync report before destructive operations, backups that you can restore, and a warning when the gate branch is ahead of an open MR or PR.
- Tasks: GPUs by vendor, `run.perf`, `run.podman_args`, `services.mode` per project, and `terok task run --debug`.
- `terok setup apparmor` and `terok setup selinux` show the command and the rules before they install.
- Agents resume their session after a restart, Codex too. `--terok-new-session` starts a new session.

### Changed

- **Breaking:** custom LLM provider files go in `~/.config/terok/providers/`. terok still reads `~/.config/terok/agent/providers/`.
- **Breaking:** a task from an earlier terok does not resume. Re-create it. `terok task restart` stops before it changes anything, and `terok sickbay` lists these tasks.
- **Breaking:** shield settings have new names: `shield.disable_firewall_no_protection` (was `shield.bypass_firewall_no_protection`), `shield.down_on_task_run` (was `shield.drop_on_task_run`), and `terok shield down --disengage` (was `--all`). An old name stops terok at startup.
- **Breaking:** an allowlist entry for a vault-protected provider endpoint no longer opens direct access. Use `shield.override`, which now also accepts a CIDR.
- The egress allowlist comes from the agent roster. terok computes the policy again on every restart.
- Where a systemd user manager runs, the supervisor of each task is a user unit. Its log is in `journalctl --user`.
- terok logs to the systemd journal when one is present.

### Fixed

- Codex through the vault: compressed responses, WebSockets and Codex Apps MCP authentication.
- DNS on hosts where AppArmor confines dnsmasq. The lookup tier accepts `drill` as well as `dig`.
- git over HTTPS in Ubuntu 24.04 images.
- The git gate after an upstream default-branch rename.
- A TUI that froze on a locked keyring, a vault probe or a suspended program.
- `terok task restart --recreate` on a container that did not stop.

### Security

- A real credential in a shared mount is an error at task start.
- The project wizard keeps the gatekeeping posture.

[0.9.0]: https://github.com/terok-ai/terok/compare/v0.8.5...v0.9.0

## v0.9.0 — Past Prologue

## Breaking Changes

1. **Custom LLM providers**: provider files go in `~/.config/terok/providers/`. terok still reads the old `~/.config/terok/agent/providers/` (#1258).
2. **Tasks**: a task from an earlier terok does not resume. Re-create it. `terok task restart` stops before it changes anything, running containers keep running, and `terok sickbay` lists the tasks to re-create (#1227, #1268).
3. **Shield settings**: `shield.bypass_firewall_no_protection` is now `shield.disable_firewall_no_protection`, `shield.drop_on_task_run` is now `shield.down_on_task_run`, and `terok shield down --all` is now `--disengage`. An allowlist entry for a vault-protected provider endpoint no longer opens direct access: use `shield.override`, which now also accepts a CIDR (#1256, #1227).

## What's Changed

<details><summary><b>Egress firewall</b>: DNS tiers, and warnings that say what the host provides</summary>

- DNS tiers name what the host provides: `dnsmasq-live`, `dnsmasq-static`, `lookup` or `getent`. terok shows a degraded tier at launch, in the task details and in `terok sickbay` (#1254, #1270, #1278). `shield.dnsmasq_path` selects the dnsmasq binary (#1278)
- DNS keeps working when AppArmor confines dnsmasq (#1254, [sandbox#520](https://github.com/terok-ai/terok-sandbox/pull/520), [shield#429](https://github.com/terok-ai/terok-shield/pull/429)). The lookup tier accepts `drill` as well as `dig` ([shield#435](https://github.com/terok-ai/terok-shield/pull/435)). The `getent` tier resolves IPv4 and IPv6 ([shield#387](https://github.com/terok-ai/terok-shield/pull/387)). Static resolution runs in parallel, with a host cache ([shield#397](https://github.com/terok-ai/terok-shield/pull/397))
- the egress allowlist comes from the agent roster, and terok computes the policy again on every restart (#1227)
- `terok setup apparmor` and `terok setup selinux` show the command, and the rules on request, before they install (#1271)
- the clearance screen shows its keys and selects the first request (#1275)
- fixes: `terok shield logs` crashed, and a sickbay hint named a flag that does not exist (#1268)

</details>

<details><summary><b>Vault, credentials and providers</b>: passphrase flows, and Codex through the vault</summary>

- set the first passphrase on a new install, and change it later from the TUI (#1183, #1194). Keep it in the kernel keyring (#1225). A locked database offers stop, re-encrypt and restart, and shows which process holds the key (#1198, #1202)
- the auth menu marks authenticated entries (#1191, #1192). A real credential in a shared mount is an error at task start, because every task container can read it (#1165)
- `terok config paths` shows the provider directory (#1258). In a task, `providers --all` lists the providers that still need authentication ([executor#466](https://github.com/terok-ai/terok-executor/pull/466)). Codex and Vibe say which provider to authenticate before they start ([executor#491](https://github.com/terok-ai/terok-executor/pull/491))
- Codex through the vault: compressed responses and WebSockets pass ([sandbox#506](https://github.com/terok-ai/terok-sandbox/pull/506), [sandbox#529](https://github.com/terok-ai/terok-sandbox/pull/529)), and Codex Apps MCP authentication works (#1283, [sandbox#548](https://github.com/terok-ai/terok-sandbox/pull/548), [executor#544](https://github.com/terok-ai/terok-executor/pull/544))
- a locked OS keyring no longer freezes the TUI ([sandbox#522](https://github.com/terok-ai/terok-sandbox/pull/522)). A vault 502 names the failure class ([sandbox#526](https://github.com/terok-ai/terok-sandbox/pull/526))
- fixes: SSH key minting (#1255), a vault probe that froze the TUI (#1257), and tasks from an older terok on a re-keyed vault (#1265, #1195)

</details>

<details><summary><b>Git gate</b>: safe sync, backups and review lag</summary>

- destructive gate operations need a safe-sync report first, in the CLI and the TUI (#1204). List, restore and delete backups from either (#1218)
- a warning when the gate branch is ahead of an open MR or PR (#1214, #1218)
- the gate follows an upstream default-branch rename ([sandbox#459](https://github.com/terok-ai/terok-sandbox/pull/459))
- the project wizard keeps the gatekeeping posture (#1185). `terok sickbay` checks for `git http-backend` (#1281)

</details>

<details><summary><b>Tasks and diagnostics</b>: GPUs, sessions, supervision and logs</summary>

- select GPUs by vendor: `nvidia`, `amd`, `intel` or `all`. The wizard has a device picker, and the docs have an NVIDIA userland recipe (#1184, #1197, #1199)
- `run.perf` grants the perfmon capability, and `run.podman_args` passes flags through (#1213). Set `services.mode` per project (#1253). `terok task run --debug` starts a task in debug mode, with a badge in the TUI (#1219, #1221)
- agents resume their session after a restart, Codex too, and `--terok-new-session` starts a new one ([executor#531](https://github.com/terok-ai/terok-executor/pull/531)). `blablador`, `kisski` and `openrouter` resume as well ([executor#499](https://github.com/terok-ai/terok-executor/pull/499))
- where a systemd user manager runs, the supervisor of each task is a user unit, with its log in `journalctl --user` (#1282). Sickbay names the cause when a task runs unsupervised, and checks the supervisor's children (#1220, #1274)
- terok logs to the systemd journal when one is present (#1226). Build and setup output is kept, like task output (#1263). Launch phases are timed (#1223). Sickbay is faster on hosts with many tasks (#1270)
- preflight warns about podman older than 4.3 ([executor#472](https://github.com/terok-ai/terok-executor/pull/472)). A full rebuild works on podman 3.x ([executor#478](https://github.com/terok-ai/terok-executor/pull/478)). git over HTTPS works in Ubuntu 24.04 images ([executor#538](https://github.com/terok-ai/terok-executor/pull/538))
- fixes: `terok task restart --recreate` on a container that did not stop (#1179, [sandbox#454](https://github.com/terok-ai/terok-sandbox/pull/454)), instructions for the image package family (#1166), refresh-agents says L1 (#1190), and task labels during a resize (#1284)

</details>

<details><summary><b>TUI</b>: tmux, suspension and small terminals</summary>

- suspend to a foreground program without a scrambled terminal or a frozen app (#1162, #1180, #1186)
- tmux gets a main-window stamp, a restart after a live upgrade, and a clearer quit prompt (#1164, #1173, #1174). The TUI returns as window 1 on resume, and `terok --tmux` starts it (#1178)
- login shows a spinner instead of a frozen screen (#1181). terok checks for upgrades when focus returns (#1182)
- the GPU modal and the SSH-key panel fit small terminals (#1200). Agent pickers show only the agents in the task image (#1201)

</details>

<details><summary><b>Under the hood</b>: build, CI, tests and hardening</summary>

- terok builds with uv, not poetry (#1147). CI uses the fleet reusable workflows (#1149, #1150, #1151, #1152), and ruff is pinned (#1234)
- terok binaries confine themselves with Landlock (#1224). Each supervisor service runs in its own hardened process, in its own Landlock lane, and stops with its container ([sandbox#450](https://github.com/terok-ai/terok-sandbox/pull/450), [sandbox#491](https://github.com/terok-ai/terok-sandbox/pull/491), [sandbox#469](https://github.com/terok-ai/terok-sandbox/pull/469))
- lazy imports make the CLI start faster (#1140). ACP moves to schema 1.16 ([executor#460](https://github.com/terok-ai/terok-executor/pull/460))
- tests and the test matrix (#1154, #1155, #1156, #1157, #1158, #1159, #1237), and refactors (#1177, #1262)
- sibling and third-party dependency updates (#1161, #1163, #1196, #1252, #1266, #1277)

</details>

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.8.5...v0.9.0

## v0.8.5 — You Exist Here

* Keep container on plain restart, make image upgrade opt-in, https://github.com/terok-ai/terok/pull/1135
* Resilient gate restart after upgrade, https://github.com/terok-ai/terok/pull/1141

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.8.4...v0.8.5

## v0.8.4 — The Celestial Temple

* Codex device-code auth login, https://github.com/terok-ai/terok/pull/1096
* UI improvements https://github.com/terok-ai/terok/pull/1097, https://github.com/terok-ai/terok/pull/1099, https://github.com/terok-ai/terok/pull/1124 
* Restart semantics changed to resume-or-recreate, https://github.com/terok-ai/terok/pull/1115

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.8.3...v0.8.4

## v0.8.3 — Start Again

hotfix: restart a task container after host reboot in https://github.com/terok-ai/terok/pull/1092

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.8.2...v0.8.3

## v0.8.2 — Locks and Hooks

Hotfix for vault passphrase error modes [1083](https://github.com/terok-ai/terok/pull/1083) and supervisor restart [1085](https://github.com/terok-ai/terok/pull/1085)

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.8.1...v0.8.2

## v0.8.1 — Emissary, Part II

Agents and providers are now independent axes — the coding harness and the LLM endpoint it talks to 
are picked separately. This enables proper support for multi-provider harnesses like Pi or OpenCode 
with any configured provider. Vault SSH keys get a routing matrix in the TUI, and the TUI itself gets 
smoother: event-driven task tracking replaces polling, so changes made outside the TUI show up live.

## What's Changed

  - **Agent × provider split & Pi support**:
     agent (claude/codex/pi/...) and provider (anthropic/openai/openrouter...) are orthogonal now, 
     so the Pi harness runs against any configured provider [#1063](https://github.com/terok-ai/terok/pull/1063);
     `terok agents dir` locates the agent config mounts [#1060](https://github.com/terok-ai/terok/pull/1060)
  - **SSH key ↔ project routing matrix**: 
     a TUI patchbay for linking, unlinking, and minting deploy keys across all projects at once, 
     replacing the one-key-at-a-time flow [#1071](https://github.com/terok-ai/terok/pull/1071);
     panic now also wipes every stored passphrase tier, not just the session unlock [#1072](https://github.com/terok-ai/terok/pull/1072);
      `terok sickbay --system` runs quick host-only checks, skipping the per-container walk [#1073](https://github.com/terok-ai/terok/pull/1073)
  - **Smoother TUI**: 
    task tracking is event-driven instead of polled, so tasks created, deleted, or finished outside the TUI are reflected live
    [#1062](https://github.com/terok-ai/terok/pull/1062), [#1056](https://github.com/terok-ai/terok/pull/1056), [#1061](https://github.com/terok-ai/terok/pull/1061)
    and interrupted deletes resume cleanly ([#1055](https://github.com/terok-ai/terok/pull/1055));
    Enter confirms multiline prompts, with hjkl navigation and a focus-aware hint 
    [#1057](https://github.com/terok-ai/terok/pull/1057), [#1067](https://github.com/terok-ai/terok/pull/1067); 
    duplicate tmux session names re-attach instead of failing [#1054](https://github.com/terok-ai/terok/pull/1054);
    the initial prompt survives dismissing the launch modal [#1078](https://github.com/terok-ai/terok/pull/1078);
    the "autopilot" run mode is renamed "unattended" ([#1059](https://github.com/terok-ai/terok/pull/1059))

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.8.0...v0.8.1

## v0.8.0 — The Emissary

**First public PyPi release**

## What's Changed

* gate NVIDIA base on host CDI presence, https://github.com/terok-ai/terok/pull/1044
* native Textual auth — API key form, OAuth via terminal, https://github.com/terok-ai/terok/pull/1042
* opt-in per-project authentication scope, https://github.com/terok-ai/terok/pull/1028
* surface AppArmor dnsmasq confinement advisory, https://github.com/terok-ai/terok/pull/1046
* per-container supervisor; retire host vault and gate daemons, https://github.com/terok-ai/terok/pull/1045
* start a new task with `t` from the project pane (#1025), https://github.com/terok-ai/terok/pull/1050


**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.7.9...v0.8.0

