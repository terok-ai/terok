# Changelog
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

