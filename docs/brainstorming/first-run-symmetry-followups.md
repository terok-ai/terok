# Follow-ups from the first-run install/uninstall symmetry arc

Parked items from the three-PR arc that shipped sandbox `sandbox setup` / `sandbox uninstall` aggregators, executor real setup/uninstall + TTY-aware preflight, and `terok uninstall`:

- sandbox: `shield uninstall-hooks` + top-level `sandbox setup`/`uninstall`
- executor: `terok-executor setup` as installer, `terok-executor uninstall`, `run` preflight with `--yes`/`--no-preflight`
- terok: `terok uninstall` aggregator

Each item below is independent enough to land on its own; listed in recommended execution order.

---

## 1. `setup.py` narrative reorder

**What.** Reshape `src/terok/cli/commands/setup.py` to match `uninstall.py`'s top-down structure — module docstring, CLI wiring (`register`/`dispatch`), `cmd_setup`, phase helpers, terminal output primitives at the bottom. Currently `setup.py` reads bottom-up: palette helpers and `_check_host_binaries` at the top, `cmd_setup` at the end.

**Why.** `uninstall.py` was written in the narrative order during its own PR; `setup.py` is the mirror file and stayed in its older bottom-up shape. The asymmetry is the most visible debt from the arc — a reader opening the pair encounters the same domain in two different reading orders.

**Scope.** One file, ~550 lines of existing code, reordered in place. No behaviour change, no test modifications. Existing tests import from the module; module-level public surface stays intact. ~150-line net change.

**Unblocks.** Item 2 (shared helpers) becomes a much smaller diff once `setup.py` has been touched for reorder anyway.

---

## 2. Shared palette + stage-line helpers

**What.** Extract the ANSI palette wrappers (`_bold` / `_green` / `_red` / `_yellow`) and the stage-line primitives (`_stage_begin`, `_status_label`) from `setup.py` and `uninstall.py` into a common module — probably `src/terok/cli/commands/_setup_ui.py` or `src/terok/lib/util/setup_ui.py`.

**Why.** Today both files carry near-identical copies. The 17-char column-padding convention in `_stage_begin` in particular is a shared visual contract that must not drift; SSOT pressure applies. CodeRabbit and the code-reuse review flagged this; it was deferred so as not to pull `setup.py` edits into the initial uninstall PR.

**Scope.** New file (~30 lines), two files trimmed to import. `_colour_on()` with its `@cache` decorator becomes the shared zero-arg helper the palette wrappers close over.

**Depends on** item 1 (cheaper if `setup.py` is already being touched).

---

## 3. Thin `terok setup` to delegate to the sandbox aggregator

**What.** Replace `terok setup`'s inline shield / vault / gate install block with a single call into `terok_sandbox.commands._handle_sandbox_setup` (or its public-API wrapper). Mirror what `terok uninstall` already does for teardown.

**Why.** Today `terok setup` has ~170 lines of phase-specific handlers that duplicate what the sandbox aggregator composes. The reason I deferred this during the uninstall arc: the current handlers do *clean-reinstall* semantics (stop → uninstall → install → verify) which the aggregator does not. Naive delegation would regress the upgrade path.

**Two ways forward:**
- **A. Extend the aggregator** to accept a `reinstall: bool = False` flag that threads through each phase's install helper. Then `terok setup` and `terok-executor setup` can both opt in. Requires a sandbox PR first, then executor + terok bumps.
- **B. Accept the regression.** Upgrades become "uninstall then install" at the operator level. Smaller diff; arguably aligns with `pipx install --force`'s "replace in place" mental model. Needs evaluation of whether any current upgrade path genuinely relies on the clean-reinstall semantics.

**Scope.** ~200-line reduction in `setup.py` under approach (B), similar with a small aggregator addition under (A).

**Depends on** items 1 and 2.

---

## 4. Promote `uninstall_global_hooks` upstream into terok-shield

**What.** Today `terok_sandbox.shield._HOOK_FILES` names the install artefacts by duplicating terok-shield's private `_HOOK_STAGES` + hook filename convention. `uninstall_hooks_direct` + `_remove_hook_files_via_sudo` then delete them by that list. The coupling is acknowledged by a local comment but it's still a coupling.

The right fix is a public `terok_shield.uninstall_global_hooks(target_dir, *, use_sudo=False)` that lives where `setup_global_hooks` lives. `terok_sandbox.shield.uninstall_hooks_direct` would then delegate.

**Why.** If terok-shield ever adds, renames, or splits a hook stage, the sandbox's `_HOOK_FILES` tuple silently becomes stale — an orphaned file would remain after `shield uninstall-hooks`. Owner-side teardown is the manifesto-correct location for this code.

**Scope.** One terok-shield PR (add `uninstall_global_hooks` + unit test), release bump, sandbox PR deleting `_HOOK_FILES` + `_remove_hook_files_via_sudo` and delegating. ~50 lines net across both repos.

**Non-urgent.** The current coupling works; this is hygiene for forward-compat, not a ship-blocker.

---

## 5. Pre-built registry-pull path (deferred, tracked elsewhere)

Explicitly not part of this arc per the pre-release conversation: registry pull waits until the overall system is consulted with Podman devs. Listed here only so a future reader seeing the "first-run takes minutes" banner knows the direction is intentional, not forgotten.

---

## Recommended execution order

Items 1 → 2 are a natural pair (one PR). Item 3 sits on top of those (separate PR, optional aggregator change in sandbox first). Item 4 is independent of the others and can be scheduled against terok-shield's own release cadence.

None are blockers for v0.8.0 or HAICON; all are debt-reduction that keeps the install/uninstall story clean as the stack evolves.
