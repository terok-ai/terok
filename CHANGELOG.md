# Changelog

<!-- Release sections are prepended by the release chain
     (terok-warden/terok-toolbox, scripts/terok-release-chain.py) from the notes
     curated for that release.  Write release prose in those notes, not here: a
     hand-kept section above the ledger is never promoted, and pushes the next
     generated section out of order. -->

## v0.10.1 — Twenty-Six Hours

Hotfix for certain upgrade paths: accept the clearance config section and report an unusable credentials section plainly in https://github.com/terok-ai/terok/pull/1355

## v0.10.0 — A Man Alone

## Upgrading

- Run `terok setup` again after upgrading. terok asks for it before it launches a task.
- Rebuild the project images with `terok project build PROJECT --refresh-agents`, then re-create the task containers. terok does not restart a container that an earlier version prepared, and `terok sickbay` lists the tasks to re-create. A headless task from an earlier terok needs a new task with a new prompt (#1304, #1337, #1345).
- Tasks no longer see the names in the host's `/etc/hosts` on podman 5.4 or later. To restore one name, add `--add-host` to `run.podman_args`. To restore all of them, set `run.hosts_file: /etc/hosts` (#1343).

## Breaking Changes

1. **Egress sets**: no curated egress set applies unless the project names it. Set `shield.sets: [recommended]` to get every built-in set; the new-project wizard offers it. The shield's `dev-standard` sample profile is no longer applied either (#1285, [sandbox#549](https://github.com/terok-ai/terok-sandbox/pull/549), [shield#448](https://github.com/terok-ai/terok-shield/pull/448)).
2. **Desktop keyring**: `credentials.use_keyring` is now `credentials.use_desktop_keyring`, the passphrase tiers are `desktop-keyring` and `session-cache`, and the transfer command is `terok vault passphrase to-desktop-keyring`. terok refuses a `config.yml` with the old key and names the new one. Stored passphrases stay where they are (#1304, #1339, [sandbox#568](https://github.com/terok-ai/terok-sandbox/pull/568), [executor#560](https://github.com/terok-ai/terok-executor/pull/560)).
3. **Auth modes**: the auth mode `oauth` is now `login`, in `terok auth` and in the TUI. An agent definition that still says `oauth` keeps loading (#1336, [executor#581](https://github.com/terok-ai/terok-executor/pull/581)).
4. **Configuration**: the Dockerfile snippet keys in the `image` section of `config.yml` never had an effect and now fail validation. A snippet belongs to `project.yml` (#1330, [executor#577](https://github.com/terok-ai/terok-executor/pull/577)). Root reads `/etc/terok/config.yml`, no longer its own `~/.config/terok/config.yml` (#1342).
5. **Editors and instructions**: `tui.external_editor` now defaults to `false`, so texts open in the integrated editor, which has a button for `$EDITOR`. Text in the global `instructions.md` now reaches tasks; before, no task received it (#1342).
6. **Removed**: the workspace diff in the TUI and the diff summary after a headless run; review changes through the git gate (#1345). Also `--max-turns` for headless runs, and `headless.max_turns_flag` in an agent definition ([executor#593](https://github.com/terok-ai/terok-executor/pull/593)).

## What's Changed

<details><summary><b>Egress firewall</b>: see what was refused, and open exactly that</summary>

- `terok shield harvest` and the harvest screen of the TUI (`h` on a task) show what the firewall refused for a task, grouped by the tier that refused it. Both work with the shield up or down (#1337, [shield#485](https://github.com/terok-ai/terok-shield/pull/485))
- `terok shield promote` and the harvest screen allow a refused target for the task, for the project (`shield.allow`), in a built-in set, or in a user set in `~/.config/terok/egress-sets/`. A target of the security tier, also a provider host that the vault relays, opens only through a `shield.override` with a reason and an expiry (#1337, [sandbox#595](https://github.com/terok-ai/terok-sandbox/pull/595), [shield#485](https://github.com/terok-ai/terok-shield/pull/485))
- `terok shield bypass --for 5m` opens a timed allow-all window for one task, so nobody has to leave the shield down. `b` on a task in the TUI says what the window does before it asks for a duration. `terok shield export --scrub` writes the harvest of a project in a form that you can share (#1337)
- mute the Allow/Deny prompts of one task: `m` on the clearance screen, `m` and `M` on a task. `services.clearance: prompt | mute | off` sets the mode globally or per project. The firewall still refuses every connection, and records it while audit is on (#1337, [clearance#267](https://github.com/terok-ai/terok-clearance/pull/267), [sandbox#595](https://github.com/terok-ai/terok-sandbox/pull/595)). An unanswered prompt expires after 24 hours, and `clearance.verdict_ttl` changes that ([clearance#256](https://github.com/terok-ai/terok-clearance/pull/256))
- curated egress sets are opt-in, and `recommended` names every built-in set (#1285). Your own sets are files in `~/.config/terok/egress-sets/` with one host on each line, and `terok shield sets` lists both kinds (#1337)
- `run.hosts_file` selects the base of a task's `/etc/hosts`; the default is `none` (#1343, [sandbox#604](https://github.com/terok-ai/terok-sandbox/pull/604)). `host.docker.internal` gets the same forwarded address as `host.containers.internal` ([shield#490](https://github.com/terok-ai/terok-shield/pull/490))
- the task pane shows the bypass countdown, the prompt mode, and the names that a degraded DNS tier pinned. `terok sickbay` warns on each DNS tier without live resolution (#1337)
- fixes: lowering the shield installs the mandatory denies in the same transaction ([shield#493](https://github.com/terok-ai/terok-shield/pull/493)), and a `+localhost:PORT` grant in a profile reaches the ruleset ([shield#473](https://github.com/terok-ai/terok-shield/pull/473))

</details>

<details><summary><b>Desktop access</b>: experimental Wayland for tasks</summary>

- `run.wayland: wl-mitm | wl-direct` gives the tasks of a project a Wayland display, so a GUI application that the agent starts opens on your desktop. The default is `none`. Both modes are experimental: set `experimental: true` or pass `--experimental` (#1351, [sandbox#611](https://github.com/terok-ai/terok-sandbox/pull/611), [executor#595](https://github.com/terok-ai/terok-executor/pull/595))
- `wl-mitm` puts the stock [wl-mitm](https://github.com/PeterCxy/wl-mitm) proxy between the task and the compositor, with a fixed allowlist of Wayland globals. terok configures and supervises the proxy; you install the binary. `wl-direct` mounts the compositor socket itself, with no filter
- every start says that desktop access increases the attack surface of the host. terok repeats startup warnings after the launch summary, where they stay on screen
- on an enforcing SELinux host, `terok setup selinux --with-wayland` and `--with-wayland-direct` each install one optional policy, and `--show` prints it first. Building them needs `selinux-policy-devel`
- a task gets its own private `XDG_RUNTIME_DIR`. A restart keeps the mode of the task and follows the current desktop session. A changed mode or compositor socket needs a new container, and terok says so instead of re-creating it

</details>

<details><summary><b>Vault, credentials and providers</b>: keyring transfers, Vibe, and a Claude that says what it needs</summary>

- moving the vault passphrase to the desktop keyring checks the passphrase, verifies what the keyring stored, and only then removes the other copies. A failure keeps them and names the step that failed (#1304, [sandbox#568](https://github.com/terok-ai/terok-sandbox/pull/568), [sandbox#599](https://github.com/terok-ai/terok-sandbox/pull/599))
- an error in the global configuration is shown as one: the project reads "blocked: global config" with the files that cause it, and Enter no longer opens `project.yml` (#1339, [sandbox#597](https://github.com/terok-ai/terok-sandbox/pull/597))
- a credential broker serves only the task that it was issued to. A revoked grant ends the connections that use it, and the SSH signer checks the key assignment before every operation (#1345, [sandbox#606](https://github.com/terok-ai/terok-sandbox/pull/606))
- Vibe: browser sign-in (#1336, [executor#581](https://github.com/terok-ai/terok-executor/pull/581)), the native model catalog through the vault (#1332, [executor#579](https://github.com/terok-ai/terok-executor/pull/579)), sessions that resume through Vibe's own hooks with the transcripts left in place (#1333, [executor#580](https://github.com/terok-ai/terok-executor/pull/580)), task instructions as system context (#1344, [executor#588](https://github.com/terok-ai/terok-executor/pull/588)), and an installation that Vibe can update itself (#1328, [executor#576](https://github.com/terok-ai/terok-executor/pull/576))
- `claude` refuses to start when it has no credential and no route to its provider, and names `terok auth claude`. A launch warns when the default agent has no stored credential, and `terok sickbay` reports it (#1337, [executor#582](https://github.com/terok-ai/terok-executor/pull/582))
- new provider: Opper ([executor#565](https://github.com/terok-ai/terok-executor/pull/565))
- fixes: an empty Bearer token gets a 401 from the vault, not a 500 ([sandbox#576](https://github.com/terok-ai/terok-sandbox/pull/576))

</details>

<details><summary><b>TUI</b>: edit the configuration it shows, and keep working while it builds</summary>

- edit `project.yml` (`y`), the Docker snippet of a project (`Shift+S`) and `config.yml` (command palette) in the TUI. terok checks a text before it writes it, and a refused text says why (#1330)
- the integrated editor highlights YAML and Markdown. **Options** (F1) lists every option with its type, default and description. With `$EDITOR` set, a button opens the text there (#1342)
- the **Instructions** screen shows how the instruction layers combine, and holds the global inherit setting (#1342)
- project initialization can be dismissed while the image builds and the gate syncs. A counter at the bottom right opens the console, where each action shows as running, waiting for input, done or failed. An SSH passphrase request waits there and does not take the focus (#1349)
- a new CLI task opens one dialog: you can edit the name while the task starts, with the launch instructions below it (#1327)
- task details use `i` for login and `v` for the shield verdict, like the main pane (#1331). Renaming a task renames its open tmux login window (#1329). Project details show the resolved base image (#1351)

</details>

<details><summary><b>Tasks, images and setup</b>: sanitizers, instructions, storage and downgrades</summary>

- `run.aslr_control` lets the processes of a task switch off ASLR for themselves, as sanitizer runs need (#1306, [sandbox#569](https://github.com/terok-ai/terok-sandbox/pull/569)). `run.perf` now also permits `perf_event_open` where podman's seccomp profile denied it ([sandbox#592](https://github.com/terok-ai/terok-sandbox/pull/592))
- the global `instructions.md` reaches tasks, and `agent.instructions` in `config.yml` sets what tasks inherit globally (#1342)
- the shell greeter of a task lists the agents that ran in it before (#1326, [executor#575](https://github.com/terok-ai/terok-executor/pull/575))
- `terok image usage` prints each row as soon as it is measured, and counts a container overlay with its task (#1320, [executor#571](https://github.com/terok-ai/terok-executor/pull/571))
- `terok setup --allow-downgrade` reinstalls host setup after a downgrade. Without the flag, terok refuses to run on host setup from a newer version, and says which (#1351, [util#190](https://github.com/terok-ai/terok-util/pull/190)). On a host that was never set up, terok says "not set up yet" instead of listing every missing file ([util#150](https://github.com/terok-ai/terok-util/pull/150))
- rootless storage: setup no longer creates shared directories with a group that podman cannot map, and a rootfs permission failure reports the directories that cause it (#1341, [util#172](https://github.com/terok-ai/terok-util/pull/172), [sandbox#600](https://github.com/terok-ai/terok-sandbox/pull/600))
- terok updates a configuration file in place: a symlinked `config.yml` stays a symlink and keeps its mode ([sandbox#593](https://github.com/terok-ai/terok-sandbox/pull/593)). Every package finds the configuration through the same search path ([util#181](https://github.com/terok-ai/terok-util/pull/181))
- the runtime of a task follows the environment, then the project, then the global configuration, everywhere. podman gets `--runtime` only when one is configured (#1348)
- Python 3.15 is supported (#1351). A home directory on eCryptfs no longer stops Landlock setup; terok reports the path as unconfined ([util#137](https://github.com/terok-ai/terok-util/pull/137))

</details>

<details><summary><b>Hardening</b>: the boundary between host and task</summary>

- what the host writes for a task and what the task may write are separate directories. Prompts and queued commands are read-only in the task, and terok reads task-written files without following links (#1345, [executor#591](https://github.com/terok-ai/terok-executor/pull/591), [executor#592](https://github.com/terok-ai/terok-executor/pull/592), [util#176](https://github.com/terok-ai/terok-util/pull/176))
- PANIC is an unconditional kill switch. It kills every managed container, stops the supervisors and purges the vault, each step with its own deadline, and it asks once (#1345, [sandbox#606](https://github.com/terok-ai/terok-sandbox/pull/606))
- terok makes output that a task controls safe before it shows it: terminal control sequences are escaped in status, diagnostics and logs, and an agent's colours survive on a terminal (#1348, [sandbox#608](https://github.com/terok-ai/terok-sandbox/pull/608))
- diagnostic commands have an output budget and a deadline, so a task cannot stall `terok sickbay` (#1348, [sandbox#608](https://github.com/terok-ai/terok-sandbox/pull/608))

</details>


**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.9.1...v0.10.0

## v0.9.1 — At Your Service

## Upgrading

- Run `terok setup` again after upgrading, and re-create containers prepared by an
  older Shield bundle.
- Setup and uninstall are serialized across the package stack, and package-owned
  setup checks stop an unsafe launch before it changes task state.

## What's Changed
* Expose SSH key defaults and public keys in CLI and TUI in https://github.com/terok-ai/terok/pull/1297
* Package-owned setup and host PATH on NixOS in https://github.com/terok-ai/terok/pull/1296
* Enable Codex HTTPS connection via the vault in https://github.com/terok-ai/terok-executor/pull/552

**Full Changelog**: https://github.com/terok-ai/terok/compare/v0.9.0...v0.9.1

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
