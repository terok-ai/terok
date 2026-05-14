# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""ProjectActionsMixin — project infrastructure actions for TerokTUI.

Handles project setup (generate, build, ssh-init, gate-sync), authentication,
and the project wizard.  Also provides shared TUI helpers used by both
project and task actions.
"""

import os
import shlex
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from textual import work

from ..lib.api import (
    delete_project,
    find_projects_sharing_gate,
    load_project,
)
from .shell_launch import launch_login

if TYPE_CHECKING:
    from textual.app import App

    from .console_log import ConsoleLogEntry

    _MixinBase = App
else:
    _MixinBase = object


class ProjectActionsMixin(_MixinBase):
    """Project infrastructure and shared action helpers for TerokTUI.

    Provides ``action_*`` methods for project-level operations (Dockerfile
    generation, image building, SSH init, gate sync, auth, wizard) as well
    as reusable helpers (``_run_suspended``, ``_launch_terminal_session``)
    used by both project and task actions.
    """

    if TYPE_CHECKING:
        # TerokTUI-specific state and helpers (not on textual.App).
        current_project_id: str | None

        async def refresh_projects(self) -> None: ...
        async def refresh_tasks(self) -> None: ...
        def _refresh_project_state(self) -> None: ...
        async def _refresh_vault_status(self, *, push_modal_if_locked: bool = False) -> None: ...
        async def _on_vault_unlock_result(self, passphrase: str | None) -> None: ...
        # Provided by ConsoleLogMixin — both are mixed into TerokTUI.
        def dispatch_console_action(
            self,
            ref: str,
            *args: object,
            title: str,
            on_complete: Callable[[ConsoleLogEntry], None] | None = None,
        ) -> ConsoleLogEntry: ...

    # ---------- Shared helpers ----------

    async def _run_suspended(
        self,
        fn: Callable[[], None],
        *,
        success_msg: str | None = None,
        refresh: str | None = "project_state",
    ) -> bool:
        """Run *fn* in a suspended TUI session with standard error handling.

        Suspends the TUI, runs *fn*, waits for the user to press Enter,
        then optionally notifies and refreshes.  Returns True if *fn*
        completed without error.  The resume prompt is shown in a finally
        block so the user always gets back to the TUI.
        """
        ok = False
        with self.suspend():
            try:
                fn()
                ok = True
            except SystemExit as e:
                print(f"Error: {e}")
            except Exception as e:
                print(f"Error: {e}")
            finally:
                input("\n[Press Enter to return to TerokTUI] ")
        if ok and success_msg:
            self.notify(success_msg)
        if refresh == "project_state":
            self._refresh_project_state()
        elif refresh == "tasks":
            await self.refresh_tasks()
        return ok

    def _run_console_action(
        self,
        ref: str,
        *args: object,
        title: str,
        refresh: str | None = "project_state",
        on_complete: Callable[[], None] | None = None,
    ) -> None:
        """Dispatch a Type-1 action as a captured child process and open its log view.

        The web-compatible replacement for ``_run_suspended``: the
        action runs in a child process (see
        [`worker_actions`][terok.tui.worker_actions]), a
        [`WorkerLogScreen`][terok.tui.worker_log_screen.WorkerLogScreen]
        shows it live — hideable to the background — and on completion
        the requested *refresh* runs plus any extra *on_complete*, even
        if the log was hidden.  *ref* is a ``"module:function"``
        reference into ``worker_actions``; *args* must be
        JSON-serialisable.

        *refresh* picks the derived state to re-pull on completion —
        ``"project_state"`` (default), ``"tasks"``, ``"vault_status"``,
        or ``None`` for no refresh.
        """
        from .worker_log_screen import WorkerLogScreen

        def _finished(_entry: object) -> None:
            if refresh == "project_state":
                self._refresh_project_state()
            elif refresh == "tasks":
                self.run_worker(self.refresh_tasks())
            elif refresh == "vault_status":
                self.run_worker(self._refresh_vault_status())
            if on_complete is not None:
                on_complete()

        entry = self.dispatch_console_action(ref, *args, title=title, on_complete=_finished)
        self.push_screen(WorkerLogScreen(entry))

    async def _launch_terminal_session(
        self,
        cmd: list[str],
        *,
        title: str,
        cname: str,
        label: str = "Opened",
    ) -> None:
        """Launch *cmd* via tmux/terminal/web, falling back to a suspended TUI.

        The in-process ``suspend()`` fallback is refused under
        textual-serve — the web TUI has no terminal to suspend *to*,
        and the attempt literally kills the served session.  Web users
        get a notification with the equivalent CLI command instead.
        """
        from .shell_launch import is_web_mode

        method, port = launch_login(cmd, title=title)

        if method == "tmux":
            self.notify(f"{label} in tmux window: {cname}")
        elif method == "terminal":
            self.notify(f"{label} in new terminal: {cname}")
        elif method == "web" and port is not None:
            self.open_url(f"http://localhost:{port}")
            self.notify(f"{label} in browser: {cname}")
        elif is_web_mode():
            self.notify(
                f"No terminal available in web mode.  Open a host shell and run "
                f"`terok login {cname}`, or start a new task in toad mode "
                f"(toad runs in the browser, no host shell needed).",
                severity="warning",
                timeout=15,
            )
        else:
            with self.suspend():
                try:
                    subprocess.run(cmd)
                except Exception as e:
                    print(f"Error: {e}")
                input("\n[Press Enter to return to TerokTUI] ")
            await self.refresh_tasks()

    # ---------- Project infrastructure actions ----------

    async def action_generate_dockerfiles(self) -> None:
        """Generate Dockerfiles for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:generate",
            pid,
            title=f"Generating Dockerfiles for {pid}",
        )

    async def action_build_images(self) -> None:
        """Build only L2 project images (reuses existing L0/L1)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:build",
            pid,
            title=f"Building images for {pid}",
            on_complete=self._invalidate_image_caches,
        )

    async def action_init_ssh(self) -> None:
        """Mint a fresh vault-backed SSH keypair for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:init_ssh",
            pid,
            title=f"Initializing SSH key for {pid}",
        )

    async def _action_build_agents(self) -> None:
        """Rebuild from L0 with fresh agents."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:build_agents",
            pid,
            title=f"Rebuilding {pid} from L0 with fresh agents",
            on_complete=self._invalidate_image_caches,
        )

    async def _action_build_full(self) -> None:
        """Rebuild from L0 (no cache)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:build_full",
            pid,
            title=f"Rebuilding {pid} from L0 (no cache)",
            on_complete=self._invalidate_image_caches,
        )

    @staticmethod
    def _invalidate_image_caches() -> None:
        """Drop cached image-label lookups after an in-TUI rebuild.

        The [`installed_agents`][terok.lib.core.images.installed_agents] lru_cache is keyed on the L1 tag,
        which a rebuild reuses — so without this, the picker would keep
        showing the previous agent set until the TUI restarts.
        """
        from ..lib.api import installed_agents

        installed_agents.cache_clear()

    async def _action_project_init(self) -> None:
        """Full project setup: ssh-init, generate, build, gate-sync."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:project_init",
            pid,
            title=f"Full setup for {pid}",
            on_complete=self._invalidate_image_caches,
        )

    # ---------- Authentication actions ----------

    async def _action_auth(self, provider: str) -> None:
        """Run the auth flow for *provider* against the selected project.

        Reached from the project-details screen, where a project is
        always selected.  The top-level "Authenticate agents and tools"
        entry uses `_action_auth_host_wide` instead — it bypasses
        ``current_project_id`` so a stray selection in the main pane
        doesn't silently scope a host-wide intent to one project.
        """
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        self._run_console_action(
            "terok.tui.worker_actions:auth",
            provider,
            self.current_project_id,
            title=f"Authenticating {provider} for {self.current_project_id}",
            refresh=None,
        )

    async def _action_auth_host_wide(self, provider: str) -> None:
        """Run the host-wide auth flow for *provider* — no project context.

        Resolves a shared L1 image (or builds one) and writes credentials
        provider-scoped to the vault, so a later switch to per-project
        auth doesn't duplicate or overwrite anything.
        """
        self._run_console_action(
            "terok.tui.worker_actions:auth",
            provider,
            None,
            title=f"Authenticating {provider} (host-wide)",
            refresh=None,
        )

    # ---------- Gate sync ----------

    async def action_sync_gate(self) -> None:
        """Manually sync gate from upstream."""
        await self._action_sync_gate()

    async def _action_sync_gate(self) -> None:
        """Sync gate (init if doesn't exist, sync if exists)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id
        self._run_console_action(
            "terok.tui.worker_actions:sync_gate",
            pid,
            title=f"Syncing gate for {pid}",
        )

    # ---------- Instructions editing ----------

    async def _action_edit_instructions(self) -> None:
        """Open project instructions.md in $EDITOR for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        def work() -> None:
            """Open instructions file in $EDITOR (creates if absent)."""
            project = load_project(pid)
            instr_path = project.root / "instructions.md"
            editor = os.environ.get("EDITOR", "").strip() or "vi"
            editor_cmd = shlex.split(editor)
            result = subprocess.run([*editor_cmd, str(instr_path)], check=False)
            if result.returncode != 0:
                raise SystemExit(f"Editor exited with code {result.returncode}")

        await self._run_suspended(work, success_msg=f"Instructions updated for {pid}")

    async def _action_toggle_instructions_inherit(self) -> None:
        """Toggle YAML instructions between inherit and override mode."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        try:
            from ..lib.util.yaml import dump as _yaml_dump, load as _yaml_load

            project = load_project(pid)
            project_yml = project.root / "project.yml"
            if not project_yml.is_file():
                self.notify("No project.yml found.")
                return
            raw = _yaml_load(project_yml.read_text(encoding="utf-8")) or {}
            agent = raw.setdefault("agent", {})
            current = agent.get("instructions")

            # Determine current mode and toggle, preserving existing custom entries
            if current is None:
                # Implicit inherit → explicit custom-only (empty)
                agent["instructions"] = []
                mode_label = "custom only (defaults disabled)"
            elif isinstance(current, list):
                items = [item for item in current if item != "_inherit"]
                if "_inherit" in current:
                    # Disable inheritance, keep existing custom entries
                    agent["instructions"] = items
                    mode_label = "custom only (defaults disabled)"
                else:
                    # Enable inheritance, preserve existing custom entries
                    agent["instructions"] = ["_inherit", *items]
                    mode_label = "inheriting defaults"
            else:
                # Scalar/dict forms — not safe to toggle automatically
                self.notify(
                    "Toggle supports list/implicit instructions only; "
                    "edit project.yml manually for this form.",
                    severity="warning",
                )
                return

            project_yml.write_text(_yaml_dump(raw), encoding="utf-8")
            self.notify(f"Instructions: {mode_label}")
        except Exception as e:
            self.notify(f"Toggle failed: {e}")
        self._refresh_project_state()

    async def _action_show_resolved_instructions(self) -> None:
        """Display fully resolved instructions as a task would receive them."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        pid = self.current_project_id

        def work() -> None:
            """Resolve and print the effective instructions."""
            from terok.lib.integrations.executor import resolve_instructions

            from ..lib.api import resolve_agent_config

            project = load_project(pid)
            effective = resolve_agent_config(
                pid, agent_config=project.agent_config, project_root=project.root
            )
            from terok.lib.integrations.executor import get_provider as _get_provider

            provider = _get_provider(None, default_agent=project.default_agent)
            text = resolve_instructions(effective, provider.name, project_root=project.root)
            print("=== Resolved Instructions ===\n")
            print(text)
            print(f"\n=== End ({len(text)} chars) ===")

        await self._run_suspended(work, refresh=None)

    async def _action_edit_global_instructions(self) -> None:
        """Open global instructions.md in $EDITOR."""

        def work() -> None:
            """Open global instructions file in $EDITOR."""
            from ..lib.api import get_config

            global_instr = get_config().global_config_path.parent / "instructions.md"
            global_instr.parent.mkdir(parents=True, exist_ok=True)
            editor = os.environ.get("EDITOR", "").strip() or "vi"
            editor_cmd = shlex.split(editor)
            result = subprocess.run([*editor_cmd, str(global_instr)], check=False)
            if result.returncode != 0:
                raise SystemExit(f"Editor exited with code {result.returncode}")

        await self._run_suspended(work, success_msg="Global instructions updated", refresh=None)

    async def _action_show_default_instructions(self) -> None:
        """Display the bundled default instructions (read-only)."""

        def work() -> None:
            """Print bundled default instructions."""
            from terok.lib.integrations.executor import bundled_default_instructions

            text = bundled_default_instructions()
            print("=== Bundled Default Instructions ===\n")
            print(text)
            print(f"\n=== End ({len(text)} chars) ===")

        await self._run_suspended(work, refresh=None)

    # ---------- OpenCode config import ----------

    async def _action_import_opencode_config(self) -> None:
        """Push the OpenCode config import modal and handle the result."""
        from .screens import OpenCodeConfigScreen

        def _on_result(result: str | None) -> None:
            """Notify the user about the import result."""
            if result:
                self.notify(f"OpenCode config imported to {result}")

        await self.push_screen(OpenCodeConfigScreen(), _on_result)

    # --- Project wizard ---

    def action_new_project_wizard(self) -> None:
        """Open the Textual-native new-project wizard.

        Kicks off the wizard flow in a Textual worker so that
        ``push_screen_wait`` — which requires an active worker — can
        sequence the three screens from one coroutine.  The old
        subprocess+suspend path was incompatible with ``textual-serve``
        (web TUI) — see issue #473.
        """
        self._run_wizard_flow()

    @work(exclusive=True, group="wizard-flow", exit_on_error=False)
    async def _run_wizard_flow(self) -> None:
        """Drive form → review → init-progress, refreshing the list at the end.

        The form/review loop preserves answers on "Back": the form
        screen accepts an *initial* prefill dict, and the review
        screen's ``REVIEW_BACK`` sentinel tells us to re-open the form
        with the user's previous input instead of starting fresh.
        ``None`` from either screen abandons the wizard.

        Any unhandled exception is surfaced as a TUI notification —
        ``exit_on_error=False`` on the decorator keeps errors from
        killing the app, but we do not want them silently vanishing
        either.
        """
        try:
            await self._run_wizard_flow_body()
        except Exception as exc:  # noqa: BLE001 — last-resort wizard error surface
            self.notify(
                f"New-project wizard failed: {exc}",
                severity="error",
                timeout=15,
            )
            raise

    async def _run_wizard_flow_body(self) -> None:
        """Inner wizard orchestration — see the decorator wrapper for docs."""
        from ..lib.api import render_project_yaml
        from .wizard_screens import (
            REVIEW_BACK,
            InitOutcome,
            InitProgressScreen,
            ProjectReviewScreen,
            WizardFormScreen,
        )

        values: dict[str, str] | None = None
        while True:
            values = await self.push_screen_wait(WizardFormScreen(initial=values))
            if values is None:
                return  # user cancelled the form

            rendered = render_project_yaml(values)
            review_result = await self.push_screen_wait(
                ProjectReviewScreen(values["project_id"], rendered)
            )
            if review_result is None:
                return  # Escape on the review screen — abandon
            if review_result is REVIEW_BACK:
                continue  # loop back to the form, prefilled
            # review_result is the (possibly edited) YAML string.
            if not isinstance(review_result, str):
                raise RuntimeError(
                    f"review screen returned unexpected type: {type(review_result).__name__}"
                )
            final_yaml = review_result
            break

        project_id = str(values["project_id"])
        outcome = await self.push_screen_wait(InitProgressScreen(project_id, final_yaml))

        match outcome:
            case InitOutcome.SUCCESS:
                self.notify(f"Project '{project_id}' is ready.")
            case InitOutcome.DECLINED:
                # User chose to keep the existing project.yml — benign
                # no-op, not an error.  No notification needed; the log
                # pane already told them what happened.
                pass
            case InitOutcome.CANCELLED:
                # User hit Esc mid-init.  project.yml may have been
                # written and early steps may have partially run; point
                # them at the CLI to resume rather than guessing.
                self.notify(
                    f"Wizard cancelled. If '{values['project_id']}' was partially "
                    "initialized, finish it with `terok project init` from the CLI.",
                    severity="warning",
                    timeout=10,
                )
            case InitOutcome.FAILED:
                self.notify(
                    f"Project '{values['project_id']}' created but init did not complete. "
                    "Fix any issues and run `terok project init` from the CLI.",
                    severity="warning",
                    timeout=10,
                )
        await self.refresh_projects()

    # --- Project delete ---

    async def _action_delete_project(self) -> None:
        """Delete the current project after confirmation."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        pid = self.current_project_id
        try:
            project = load_project(pid)
        except (SystemExit, Exception) as e:
            self.notify(f"Error loading project: {e}")
            return

        # Build confirmation message
        lines = [
            f"Delete project '{pid}'?\n",
            f"Config root: {project.root}",
            f"Security class: {project.security_class}",
        ]
        if project.upstream_url:
            lines.append(f"Upstream: {project.upstream_url}")

        sharing = find_projects_sharing_gate(project.gate_path, exclude_project=pid)
        if sharing:
            names = ", ".join(p for p, _ in sharing)
            lines.append(f"\nNote: gate is shared with: {names} (will NOT be deleted)")

        from ..lib.api import get_config

        archive_path = get_config().archive_dir
        lines.append("\nAll project data will be permanently deleted.")
        lines.append("Project config, task data, and build artifacts will be archived at:")
        lines.append(f"{archive_path}")

        from .screens import ConfirmDestructiveScreen

        await self.push_screen(
            ConfirmDestructiveScreen(
                message="\n".join(lines),
                title=f"Delete Project: {pid}",
            ),
            self._on_delete_project_confirmed,
        )

    async def _on_delete_project_confirmed(self, confirmed: bool | None) -> None:
        """Handle the result of the delete confirmation dialog."""
        if not confirmed or not self.current_project_id:
            return

        pid = self.current_project_id
        try:
            result = delete_project(pid)
        except (SystemExit, Exception) as e:
            self.notify(f"Delete failed: {e}")
            return

        msg = f"Project '{pid}' deleted."
        if result.get("archive"):
            msg += f" Archive: {result['archive']}"
        if result.get("skipped"):
            msg += f" ({len(result['skipped'])} item(s) skipped)"
        self.notify(msg)

        self.current_project_id = None
        await self.refresh_projects()

    # ---------- Gate server actions ----------

    async def _action_gate_install(self) -> None:
        """Install systemd socket units for the gate server."""
        self._run_console_action(
            "terok.tui.worker_actions:gate_install",
            title="Installing gate server systemd units",
        )

    async def _action_gate_uninstall(self) -> None:
        """Uninstall systemd units for the gate server."""
        self._run_console_action(
            "terok.tui.worker_actions:gate_uninstall",
            title="Uninstalling gate server systemd units",
        )

    async def _action_gate_start(self) -> None:
        """Start the gate server daemon."""
        self._run_console_action(
            "terok.tui.worker_actions:gate_start",
            title="Starting gate server daemon",
        )

    async def _action_gate_stop(self) -> None:
        """Stop the gate server daemon."""
        self._run_console_action(
            "terok.tui.worker_actions:gate_stop",
            title="Stopping gate server daemon",
        )

    # ---------- Shield actions ----------

    async def _action_shield_setup(self) -> None:
        """Push shield setup modal and run hook installation on result."""
        from .screens import ShieldSetupScreen

        await self.push_screen(ShieldSetupScreen(), self._on_shield_setup_result)

    async def _on_shield_setup_result(self, result: str | None) -> None:
        """Run hook installation after shield setup modal choice."""
        if result is None:
            return
        self._run_console_action(
            "terok.tui.worker_actions:shield_setup",
            result == "root",
            title="Installing shield hooks",
        )

    # ---------- Vault actions ----------

    async def _action_vault_install(self) -> None:
        """Install systemd socket activation for the vault."""
        self._run_console_action(
            "terok.tui.worker_actions:vault_install",
            title="Installing vault systemd socket",
        )

    async def _action_vault_uninstall(self) -> None:
        """Uninstall vault systemd units."""
        self._run_console_action(
            "terok.tui.worker_actions:vault_uninstall",
            title="Uninstalling vault systemd units",
        )

    async def _action_vault_start(self) -> None:
        """Generate routes and start the vault daemon."""
        self._run_console_action(
            "terok.tui.worker_actions:vault_start",
            title="Starting vault",
        )

    async def _action_vault_stop(self) -> None:
        """Stop the vault daemon."""
        self._run_console_action(
            "terok.tui.worker_actions:vault_stop",
            title="Stopping vault",
        )

    async def _action_vault_unlock(self) -> None:
        """Prompt for the SQLCipher passphrase and land it on the session-file tier.

        Re-uses the same modal as the on-mount probe, then funnels the
        result through ``_on_vault_unlock_result`` so the write +
        re-probe + pill refresh all stay in one place.
        """
        from .screens import VaultUnlockModal

        await self.push_screen(VaultUnlockModal(), self._on_vault_unlock_result)

    async def _action_vault_lock(self) -> None:
        """Clear the session-file and stop the daemon (reversible).

        Persistent tiers (keyring, sealed systemd-creds,
        ``credentials.passphrase``) are intentionally untouched — the
        TUI's lock action is the reversible one, ``vault lock --forget``
        from a shell remains the destructive escape hatch.
        """
        self._run_console_action(
            "terok.tui.worker_actions:vault_lock",
            title="Locking vault (clearing session tier)",
            refresh="vault_status",
        )

    async def _action_vault_seal(self) -> None:
        """Seal the currently resolved passphrase into a systemd-creds credential.

        Defers to sandbox's ``_handle_vault_seal`` with the default
        ``--key=auto`` so a TPM2-equipped host gets ``host+tpm2``
        binding automatically.  The sealed-key-mode output streams into
        the log view where the operator can read it.
        """
        self._run_console_action(
            "terok.tui.worker_actions:vault_seal",
            title="Sealing vault passphrase into systemd-creds",
            refresh="vault_status",
        )
