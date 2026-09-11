# Changelog
## Unreleased

* **Behavior change — auth-protect opt-out moved**: adding a vault-protected
  provider endpoint to a custom allowlist profile (the
  [#566](https://github.com/terok-ai/terok/issues/566) flow) no longer
  re-enables direct access; profile entries now compose below the shield's
  security-deny tier, which always wins.  Use the new per-project
  `shield.override` break-glass entry (host + reason + optional expiry)
  instead — see the Shield Security guide.
* **Behavior change — nothing applied implicitly**: terok tasks compose no
  shield profile, and no curated egress set unless `shield.sets` names
  one.  While the shield is up, a project without `shield.sets` reaches
  only its git remote, its agents' provider endpoints, and its own
  `shield.allow` hosts.  Add `shield.sets: [recommended]` to `project.yml`
  (or run `terok shield sets <project> --set recommended`) for git
  hosting, language and container registries, and OS package repos; the
  new-project wizard offers it.  Move hosts from a customized shield
  profile under `~/.config/terok/shield/profiles` to `shield.allow`.
  `config.yml` rejects the `shield.profiles` key, which never took
  effect: remove it, because terok falls back to defaults for the whole
  file when `config.yml` fails validation.
* **Upgrade contract**: containers created by a different shield-bundle
  generation refuse to resume (`terok task restart` fails fast before
  anything is stopped; `terok sickbay` diagnoses them).  Re-create the task
  to move it to the current bundle; running containers keep running
  untouched.
* Shield policy tiers are now recomputed from the current roster and project
  config on every plain restart, not only at container creation.

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

