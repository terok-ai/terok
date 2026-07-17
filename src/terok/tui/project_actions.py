# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""ProjectActionsMixin — project infrastructure actions for TerokTUI.

Handles project setup (generate, build, ssh-init, gate-sync), authentication,
and the project wizard.  Also provides shared TUI helpers used by both
project and task actions.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import shlex
import sys
import termios
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from textual import work

from ..lib.api import (
    delete_project,
    find_projects_sharing_gate,
    load_project,
)
from .shell_launch import launch_login

#: Prompt shown to the user after a suspended-TUI subprocess exits — the
#: blocking input reads any key so the user has time to see the
#: container's last lines before the TUI redraws over them.
_RESUME_PROMPT = "\n[Press Enter to return to TerokTUI] "

if TYPE_CHECKING:
    from textual.app import App

    from ..lib.api import AuthSession
    from .console_log import ConsoleLogEntry

    _MixinBase = App
else:
    _MixinBase = object

#: Indices into the ``termios.tcgetattr`` attribute list.
_IFLAG = 0
_LFLAG = 3


@contextmanager
def _terminal_pollution_guard() -> Iterator[None]:
    """Shield the TUI from terminal state a foreground child leaves behind.

    Interactive children run under a suspended TUI (agent CLIs, editors,
    ``podman attach``) share stdin/stdout with the app, and some exit
    without undoing their terminal tweaks.  Two of those poisons outlive
    the child: a tty left in raw mode swallows the Enter that should
    complete ``_RESUME_PROMPT`` (``\\r`` is never translated to ``\\n``),
    and ``O_NONBLOCK`` left on
    stdout kills Textual's writer thread with a ``BlockingIOError`` on
    the first write after resume — its bounded queue then fills and
    wedges the whole app.  Node-based agent CLIs are known to do both.

    Snapshots the tty attributes on entry; on exit clears ``O_NONBLOCK``
    from stdin and stdout and restores the snapshot with canonical line
    input re-forced (in case the snapshot itself was already polluted).
    A no-op wherever there is no real tty to guard.
    """
    try:
        stdin_fd = sys.stdin.fileno()
        saved = termios.tcgetattr(stdin_fd)
    except (ValueError, OSError, termios.error):
        stdin_fd, saved = -1, None
    try:
        yield
    finally:
        for stream in (sys.stdin, sys.stdout):
            with suppress(ValueError, OSError):
                fd = stream.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                if flags & os.O_NONBLOCK:
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        if saved is not None:
            saved[_IFLAG] |= termios.ICRNL
            saved[_LFLAG] |= termios.ICANON | termios.ECHO
            with suppress(termios.error):
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)


class ProjectActionsMixin(_MixinBase):
    """Project infrastructure and shared action helpers for TerokTUI.

    Provides ``action_*`` methods for project-level operations (Dockerfile
    generation, image building, SSH init, gate sync, auth, wizard) as well
    as reusable helpers (``_run_console_action``, ``_launch_terminal_session``)
    used by both project and task actions.
    """

    if TYPE_CHECKING:
        # TerokTUI-specific state and helpers (not on textual.App).
        current_project_name: str | None

        async def refresh_projects(self) -> None: ...
        async def refresh_tasks(self) -> None: ...
        def _refresh_project_state(self) -> None: ...
        async def _refresh_vault_status(self, *, push_modal_if_locked: bool = False) -> None: ...
        async def _on_vault_unlock_result(self, passphrase: str | None) -> None: ...
        # ``@work``-decorated on the App, so the real return is a Worker.
        def _run_vault_provision_flow(self) -> object: ...
        def _run_vault_change_flow(self) -> object: ...
        # Provided by ConsoleLogMixin — both are mixed into TerokTUI.
        def dispatch_console_action(
            self,
            ref: str,
            *args: object,
            title: str,
            on_complete: Callable[[ConsoleLogEntry], None] | None = None,
            env: dict[str, str] | None = None,
        ) -> ConsoleLogEntry: ...

    # ---------- Shared helpers ----------

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
        """Launch *cmd* via tmux/terminal, falling back to a suspended TUI.

        Inside tmux, a window already logged into *cname* is switched to
        instead of opening a duplicate — every login command attaches to
        the same tmux session *inside* the container, so one host window
        per container is always enough.

        Hard-gated on ``App.is_web``: a container login attaches a host
        terminal, and under textual-serve there is none — the in-process
        ``suspend()`` fallback would literally kill
        the served session.  Web users get an error notification instead;
        the local-terminal paths (tmux / desktop terminal / suspend) are
        unchanged.
        """
        if self.is_web:
            self.notify(
                f"CLI login is unavailable in the web TUI — it needs a host "
                f"terminal.  Open a host shell and run `terok login {cname}`, "
                f"or start a task in toad mode (toad serves in the browser).",
                severity="error",
                timeout=12,
            )
            return

        # Threaded: launch_login shells out to tmux / ps / terminal
        # spawners — each call is timeout-capped, but even a few capped
        # probes back-to-back would stall the loop for whole seconds.
        method, _port = await asyncio.to_thread(launch_login, cmd, title=title, reuse_key=cname)

        if method == "tmux-existing":
            self.notify(f"Switched to existing tmux window: {cname}")
        elif method == "tmux":
            self.notify(f"{label} in tmux window: {cname}")
        elif method == "terminal":
            self.notify(f"{label} in new terminal: {cname}")
        else:
            await self._run_suspended(*cmd)
            await self.refresh_tasks()

    async def _run_suspended(self, *argv: str, prompt_on_success: bool = True) -> int | None:
        """Run *argv* in the foreground with the TUI suspended.

        The single home of the suspend dance shared by container logins,
        OAuth containers and the external editor.  The child runs as an
        asyncio subprocess and the resume prompt reads in a thread: the
        app is suspended, not stopped, and an interactive session can
        last minutes — blocking the loop for its whole duration would
        freeze every background worker and timer with it.  The terminal
        is scrubbed after the child exits
        ([`_terminal_pollution_guard`][terok.tui.project_actions._terminal_pollution_guard])
        so the prompt and the TUI resume get a sane tty, and the prompt
        keeps the child's last lines readable before the TUI redraws
        over them.  Failures — launch errors and non-zero exits — always
        prompt so the error stays visible; clean (zero) exits prompt
        unless *prompt_on_success* is false.  Repaints are batched for the
        entire suspension — a background render against the suspended
        driver would wedge the whole app (see the in-body comment).

        Returns:
            The child's exit code, or ``None`` if it could not be launched.
        """
        # ``batch_update`` keeps the app from rendering for the whole
        # suspension.  ``suspend()`` stops the driver's writer thread but the
        # loop deliberately keeps running, so anything that still repaints —
        # a spinner, a podman-events refresh — feeds the writer's bounded
        # queue that nothing drains any more; once it fills, ``queue.put``
        # blocks the event loop itself and the app can never observe the
        # child's exit.  Possibly a workaround for a Textual-level flaw
        # (writes to a suspended driver deadlock instead of failing loud);
        # the real cause deserves a later upstream investigation.
        with self.suspend(), self.batch_update():
            code: int | None
            with _terminal_pollution_guard():
                try:
                    proc = await asyncio.create_subprocess_exec(*argv)
                    code = await proc.wait()
                except (OSError, ValueError) as exc:
                    print(f"Error: {exc}")
                    code = None
            if prompt_on_success or code != 0:
                with suppress(EOFError):
                    await asyncio.to_thread(input, _RESUME_PROMPT)
        return code

    # ---------- Project infrastructure actions ----------

    async def action_generate_dockerfiles(self) -> None:
        """Generate Dockerfiles for the current project."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        self._run_console_action(
            "terok.tui.worker_actions:generate",
            pid,
            title=f"Generating Dockerfiles for {pid}",
        )

    async def action_build_images(self) -> None:
        """Build only L2 project images (reuses existing L0/L1)."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        self._run_console_action(
            "terok.tui.worker_actions:build",
            pid,
            title=f"Building images for {pid}",
        )

    async def action_init_ssh(self) -> None:
        """Mint a fresh vault-backed SSH keypair for the current project."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        self._run_console_action(
            "terok.tui.worker_actions:init_ssh",
            pid,
            title=f"Initializing SSH key for {pid}",
        )

    async def _action_build_agents(self) -> None:
        """Rebuild from L1 with fresh agents."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        self._run_console_action(
            "terok.tui.worker_actions:build_agents",
            pid,
            title=f"Rebuilding {pid} from L1 with fresh agents",
        )

    async def _action_build_full(self) -> None:
        """Rebuild from L0 (no cache)."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        self._run_console_action(
            "terok.tui.worker_actions:build_full",
            pid,
            title=f"Rebuilding {pid} from L0 (no cache)",
        )

    async def _action_project_init(self) -> None:
        """Re-run full project setup: ssh-init, generate, build, gate-sync.

        Reuses the wizard's
        [`InitProgressScreen`][terok.tui.wizard_screens.InitProgressScreen]
        against the existing ``project.yml`` — same per-step badges and
        log pane, and crucially the **interactive deploy-key
        registration pause** (with the full public key on screen).
        Skipping that pause would let gate-sync run before the key is
        registered upstream and hang on a long timeout — so a single
        captured child process is the wrong shape for this action.
        """
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        from .wizard_screens import InitProgressScreen

        def _on_init_done(_outcome: object) -> None:
            self._refresh_project_state()

        await self.push_screen(InitProgressScreen(self.current_project_name), _on_init_done)

    # ---------- Authentication actions ----------

    async def _action_auth(self, provider: str) -> None:
        """Run the auth flow for *provider* against the selected project.

        Reached from the project-details screen, where a project is
        always selected.  The top-level "Authenticate agents and tools"
        entry uses ``_action_auth_host_wide`` instead — it bypasses
        ``current_project_name`` so a stray selection in the main pane
        doesn't silently scope a host-wide intent to one project.
        """
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        self._run_auth_flow(provider, self.current_project_name)

    async def _action_auth_host_wide(self, provider: str) -> None:
        """Run the host-wide auth flow for *provider* — no project context.

        Resolves a shared L1 image (without building) and writes
        credentials provider-scoped to the vault, so a later switch to
        per-project auth doesn't duplicate or overwrite anything.
        """
        self._run_auth_flow(provider, None)

    @work(exclusive=False, group="auth-flow", exit_on_error=False)
    async def _run_auth_flow(self, provider: str, project_name: str | None) -> None:
        """Drive the Textual-native auth flow in a worker.

        Thin ``@work`` wrapper around
        [`_run_auth_flow_body`][terok.tui.project_actions.ProjectActionsMixin._run_auth_flow_body];
        the worker context is what lets the body sequence modal screens via
        ``push_screen_wait``.  Split out so the body's mode-selection logic
        is unit-testable without a running worker (mirrors the wizard's
        ``_run_wizard_flow`` / ``_run_wizard_flow_body`` pair).
        """
        await self._run_auth_flow_body(provider, project_name)

    async def _run_auth_flow_body(self, provider: str, project_name: str | None) -> None:
        """Pick the auth mode for *provider* and dispatch to the matching flow.

        Replaces the previous ``worker_actions:auth`` subprocess dispatch,
        which couldn't reach a stdin (the worker runs with
        ``stdin=DEVNULL``) so the executor's ``prompt_toolkit`` API-key
        prompt always saw EOF.  The OAuth path reuses the same
        tmux/terminal/suspend cascade as project shell logins.
        """
        from ..lib.api import AUTH_PROVIDERS, available_auth_modes
        from .screens import AuthModeScreen

        if provider not in AUTH_PROVIDERS:
            self.notify(f"Unknown auth provider: {provider}", severity="error")
            return

        modes = available_auth_modes(provider)
        if not modes:
            self.notify(
                f"Auth for {provider!r} requires OAuth, but it is disabled "
                "by the experimental / allow_oauth gates in config.yml.",
                severity="error",
                timeout=10,
            )
            return

        mode: str | None
        if len(modes) == 1:
            mode = modes[0]
        else:
            mode = await self.push_screen_wait(AuthModeScreen(provider))
            if mode is None:
                return

        if mode == "api_key":
            await self._auth_via_api_key(provider, project_name=project_name)
        else:
            # "oauth" launches the browser callback; "device_auth" runs the
            # same login headlessly via a device code.
            await self._auth_via_oauth(
                provider, project_name=project_name, device_auth=(mode == "device_auth")
            )

    async def _auth_via_api_key(self, provider: str, *, project_name: str | None) -> None:
        """Collect an API key via a Textual modal and store it in the vault."""
        from ..lib.api import resolve_credential_routing, store_api_key
        from .screens import ApiKeyEntryScreen

        key = await self.push_screen_wait(ApiKeyEntryScreen(provider))
        if not key:
            return
        # Per-project projects store under their own vault set; shared/host-wide
        # land in "default".  Same routing the CLI uses (see domain.auth).
        _mounts_dir, credential_set = resolve_credential_routing(project_name)
        try:
            store_api_key(provider, key, credential_set=credential_set)
        except Exception as exc:  # noqa: BLE001 — surface every storage failure
            self.notify(
                f"Failed to store API key for {provider}: {exc}",
                severity="error",
                timeout=10,
            )
            return
        self.notify(f"API key stored for {provider}.")

    async def _auth_via_oauth(
        self, provider: str, *, project_name: str | None, device_auth: bool = False
    ) -> None:
        """Prepare an OAuth auth container session and hand it to the launcher.

        With *device_auth* the prepared session runs the provider's headless
        device-code login instead of the browser-callback flow.
        """
        from ..lib.api import (
            Authenticator,
            auth_image_staleness_warning,
            find_host_auth_image,
            resolve_credential_routing,
        )

        # Heads-up before launching the auth container: a stale project image
        # bakes outdated login scripts (host-wide returns None — no warning).
        if (warning := auth_image_staleness_warning(project_name)) is not None:
            self.notify(warning, severity="warning", timeout=10)
        from ..lib.core.config import (
            is_claude_oauth_exposed,
            is_codex_oauth_exposed,
        )
        from ..lib.core.images import project_cli_image

        if project_name is None:
            image = find_host_auth_image(provider)
            if image is None:
                self.notify(
                    f"No agent image found for OAuth auth of {provider}.  Run "
                    "`terok image build` first, or use the per-project entry.",
                    severity="warning",
                    timeout=12,
                )
                return
        else:
            image = project_cli_image(project_name)

        expose = (provider == "claude" and is_claude_oauth_exposed()) or (
            provider == "codex" and is_codex_oauth_exposed()
        )
        # Per-project projects capture into their own mount tree + vault set;
        # shared/host-wide use the global tree + "default" (same routing the
        # CLI uses, see domain.auth.resolve_credential_routing).
        mounts_dir, credential_set = resolve_credential_routing(project_name)
        # ``prepare_oauth`` raises ``SystemExit`` only for an unknown or
        # non-OAuth provider — both already excluded by ``_run_auth_flow``
        # before we get here, so no defensive catch is needed.  An
        # unexpected raise surfaces through the ``@work`` harness instead
        # of tearing down the app.
        session = Authenticator(provider).prepare_oauth(
            project_name,
            mounts_dir=mounts_dir,
            image=image,
            expose_token=expose,
            credential_set=credential_set,
            device_auth=device_auth,
        )
        await self._launch_oauth_container(session)

    async def _launch_oauth_container(self, session: AuthSession) -> None:
        """Launch ``session.argv`` via tmux/terminal/suspend; capture on exit.

        Mirrors
        [`_launch_terminal_session`][terok.tui.project_actions.ProjectActionsMixin._launch_terminal_session]
        but ends with a capture step: extract credentials from the
        session's temp dir and write them to the vault.  The
        tmux/terminal branches dispatch the capture into a background
        worker (the user closes the spawned terminal whenever they're
        done); the suspend branch captures inline.
        """
        if self.is_web:
            self.notify(
                "OAuth auth needs a host terminal — unavailable in the web TUI.  "
                "Use API-key auth instead, or run `terok auth` on the host.",
                severity="error",
                timeout=12,
            )
            session.cleanup()
            return

        method, _port = launch_login(session.argv, title=session.title)
        if method in ("tmux", "terminal"):
            self.notify(
                f"OAuth container started in new {method}.  Credentials "
                "will be captured automatically when it exits.",
                timeout=8,
            )
            self._watch_auth_session(session)
            return

        code = await self._run_suspended(*session.argv)
        self._capture_auth_session(session, exit_code=1 if code is None else code)

    @work(exclusive=False, group="auth-watch", exit_on_error=False)
    async def _watch_auth_session(self, session: AuthSession) -> None:
        """Background: wait for the auth container to exit, then capture.

        Polls ``podman container exists`` so the launched terminal has time
        to start the container, then blocks on ``podman wait`` and forwards
        the exit code to ``_capture_auth_session``.
        """
        # 2-minute startup grace: the user may take a moment to interact
        # with the spawned tmux/terminal window before the container is up.
        for _ in range(120):
            check = await asyncio.create_subprocess_exec(
                "podman",
                "container",
                "exists",
                session.container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if (await check.wait()) == 0:
                break
            await asyncio.sleep(1)
        else:
            self.notify(
                f"Auth container {session.container_name!r} never started — cleanup only.",
                severity="warning",
                timeout=10,
            )
            session.cleanup()
            return

        wait_proc = await asyncio.create_subprocess_exec(
            "podman",
            "wait",
            session.container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await wait_proc.communicate()
        try:
            exit_code = int(stdout.decode().strip())
        except ValueError:
            # ``UnicodeDecodeError`` is a ``ValueError`` subclass — caught here too.
            exit_code = 1
        self._capture_auth_session(session, exit_code=exit_code)

    def _capture_auth_session(self, session: AuthSession, *, exit_code: int) -> None:
        """Run ``session.capture()`` if the container exited cleanly; always cleanup."""
        try:
            if exit_code != 0:
                self.notify(
                    f"OAuth container exited {exit_code} — credentials not captured.",
                    severity="warning",
                    timeout=10,
                )
                return
            session.capture()
            self.notify(f"Credentials captured for {session.provider.name}.")
        except Exception as exc:  # noqa: BLE001 — surface any extractor / vault error
            self.notify(
                f"Capture failed for {session.provider.name}: {exc}",
                severity="error",
                timeout=10,
            )
        finally:
            session.cleanup()

    # ---------- Gate sync ----------

    async def action_sync_gate(self) -> None:
        """Manually sync gate from upstream."""
        await self._action_sync_gate()

    async def _action_sync_gate(self) -> None:
        """Sync gate (init if doesn't exist, sync if exists)."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        self._run_console_action(
            "terok.tui.worker_actions:sync_gate",
            pid,
            title=f"Syncing gate for {pid}",
        )

    # ---------- Instructions editing ----------

    async def _edit_instructions_file(self, instr_path: Path, *, title: str, done_msg: str) -> None:
        """Edit *instr_path* — in ``$EDITOR`` or the integrated editor.

        ``$EDITOR`` is used when it is set, the ``tui.external_editor``
        preference is on (the default), and the TUI is *not* web-served
        — opening it suspends the TUI, which is fine in a real terminal
        but impossible under textual-serve.  Otherwise (web TUI, the
        preference off, or no ``$EDITOR``) the file opens in the
        integrated [`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen].
        """
        from ..lib.api import get_config

        editor = "" if self.is_web else os.environ.get("EDITOR", "").strip()
        if editor and get_config().tui_external_editor:
            await self._edit_in_external_editor(instr_path, editor, done_msg=done_msg)
            return

        from .text_screens import TextEditorScreen

        existing = instr_path.read_text(encoding="utf-8") if instr_path.is_file() else ""

        def _on_saved(text: str | None) -> None:
            if text is None:
                return
            instr_path.parent.mkdir(parents=True, exist_ok=True)
            instr_path.write_text(text, encoding="utf-8")
            self.notify(done_msg)
            self._refresh_project_state()

        await self.push_screen(TextEditorScreen(existing, title=title), _on_saved)

    async def _edit_in_external_editor(
        self, instr_path: Path, editor: str, *, done_msg: str
    ) -> None:
        """Open *instr_path* in ``$EDITOR`` via a suspended terminal.

        Local-terminal only — the caller forces the integrated editor
        under ``App.is_web``, so the ``suspend()`` here always has a real
        terminal to suspend *to* (the same local-only suspend the CLI
        login path keeps).
        """
        instr_path.parent.mkdir(parents=True, exist_ok=True)
        code = await self._run_suspended(
            *shlex.split(editor), str(instr_path), prompt_on_success=False
        )
        if code != 0:
            return
        self.notify(done_msg)
        self._refresh_project_state()

    async def _action_edit_instructions(self) -> None:
        """Edit the current project's instructions.md in ``$EDITOR`` or the integrated editor."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        instr_path = load_project(pid).root / "instructions.md"
        await self._edit_instructions_file(
            instr_path,
            title=f"Instructions — {pid}",
            done_msg=f"Instructions updated for {pid}",
        )

    async def _action_toggle_instructions_inherit(self) -> None:
        """Toggle YAML instructions between inherit and override mode."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name

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
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name

        from terok.lib.api.agents import get_agent, resolve_instructions

        from ..lib.api import resolve_agent_config
        from .text_screens import TextViewScreen

        project = load_project(pid)
        effective = resolve_agent_config(
            pid, agent_config=project.agent_config, project_root=project.root
        )
        provider = get_agent(None, default_agent=project.default_agent)
        text = resolve_instructions(
            effective, provider.name, project_root=project.root, family=project.known_family
        )
        await self.push_screen(TextViewScreen(text, title=f"Resolved instructions — {pid}"))

    async def _action_edit_global_instructions(self) -> None:
        """Edit the global instructions.md in ``$EDITOR`` or the integrated editor."""
        from ..lib.api import get_config

        global_instr = get_config().global_config_path.parent / "instructions.md"
        await self._edit_instructions_file(
            global_instr,
            title="Global instructions",
            done_msg="Global instructions updated",
        )

    async def _action_show_default_instructions(self) -> None:
        """Display the bundled default instructions (read-only)."""
        from terok.lib.api.agents import bundled_default_instructions

        from .text_screens import TextViewScreen

        text = bundled_default_instructions()
        await self.push_screen(TextViewScreen(text, title="Bundled default instructions"))

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
                ProjectReviewScreen(values["project_name"], rendered)
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

        project_name = str(values["project_name"])
        outcome = await self.push_screen_wait(InitProgressScreen(project_name, final_yaml))

        match outcome:
            case InitOutcome.SUCCESS:
                self.notify(f"Project '{project_name}' is ready.")
                self._maybe_nudge_global_agents(values)
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
                    f"Wizard cancelled. If '{values['project_name']}' was partially "
                    "initialized, finish it with `terok project init` from the CLI.",
                    severity="warning",
                    timeout=10,
                )
            case InitOutcome.FAILED:
                self.notify(
                    f"Project '{values['project_name']}' created but init did not complete. "
                    "Fix any issues and run `terok project init` from the CLI.",
                    severity="warning",
                    timeout=10,
                )
        await self.refresh_projects()

    def _maybe_nudge_global_agents(self, values: dict[str, str]) -> None:
        """Notify with a command-palette hint when neither scope configures agents.

        Silent when the project overrode the default *or* the global is
        already set — both states already produce a deliberate roster.
        """
        if values.get("agents"):
            return
        # Best-effort advisory: anything that goes wrong probing the
        # global config should be swallowed.  The wizard already
        # succeeded; a probe failure must not look like a failed run.
        try:
            from terok.lib.integrations.executor import ExecutorConfigView

            if ExecutorConfigView.image_agents():
                return
        except Exception:  # noqa: BLE001 — advisory probe, must not surface
            return
        self.notify(
            "Tip: no default agents are configured.  "
            "Open 'Set default agents' from the command palette (Ctrl+P) "
            "to pick the roster baked into L1 by default.",
            timeout=15,
        )

    # --- Project delete ---

    async def _action_delete_project(self) -> None:
        """Delete the current project after confirmation."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return

        pid = self.current_project_name
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
        if not confirmed or not self.current_project_name:
            return

        pid = self.current_project_name
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

        self.current_project_name = None
        await self.refresh_projects()

    # ---------- Shield actions ----------

    async def _action_shield_setup(self) -> None:
        """Install shield OCI hooks into the canonical terok-owned dir."""
        self._run_console_action(
            "terok.tui.worker_actions:shield_setup",
            title="Installing shield hooks",
        )

    # ---------- Vault actions ----------
    #
    # No daemon-lifecycle actions: vault is now a per-container proxy
    # spawned by the supervisor — there's nothing on the host to
    # install / uninstall / start / stop.

    async def _action_vault_unlock(self) -> None:
        """Prompt for the SQLCipher passphrase and land it on the session-file tier.

        Re-uses the same modal as the on-mount probe, then funnels the
        result through ``_on_vault_unlock_result`` so the write +
        re-probe + pill refresh all stay in one place.

        On an UNPROVISIONED vault there is nothing to unlock — the
        modal would accept any string and silently make it the future
        vault's key on the reboot-volatile session tier.  Route to the
        first-passphrase provisioning flow instead, the same chooser +
        create + reveal conversation setup runs.  The classification
        comes from the shared snapshot, not a hand-rolled
        ``db_path.exists()`` probe.
        """
        from terok.lib.api.vault import VaultState, load_vault_status

        from .screens import VaultUnlockModal

        try:
            state = load_vault_status().state
        except Exception:  # noqa: BLE001 — fall through to the unlock prompt on a broken probe
            state = VaultState.LOCKED
        if state is VaultState.UNPROVISIONED:
            self._run_vault_provision_flow()
            return
        await self.push_screen(VaultUnlockModal(), self._on_vault_unlock_result)

    async def _action_vault_lock(self) -> None:
        """Lock the vault — clear every stored copy of the passphrase.

        Locking removes the session file *and* every durable tier
        (keyring, sealed systemd-creds, plaintext config): against a
        machine-bound tier a soft-lock would just auto-unlock on the next
        access (the BitLocker-Suspend trap), so the only honest lock is
        eviction.  Reversible only by re-supplying the passphrase, so it's
        gated behind a confirmation modal — the TUI can't run the shell's
        typed-``SAVED`` prompt.
        """
        from .screens import ConfirmDestructiveScreen

        await self.push_screen(
            ConfirmDestructiveScreen(
                message=(
                    "This clears EVERY stored copy of the vault passphrase — the "
                    "session file, the OS keyring, and the sealed systemd-creds "
                    "credential.\n\n"
                    "You will need your saved passphrase to unlock again. If you "
                    "have not saved it off-host, the vault becomes unrecoverable."
                ),
                title="Lock vault",
                confirm_label="Lock",
            ),
            self._on_vault_lock_confirmed,
        )

    def _on_vault_lock_confirmed(self, confirmed: bool | None) -> None:
        """Run the lock worker once the operator confirms the destructive clear."""
        if not confirmed:
            return
        self._run_console_action(
            "terok.tui.worker_actions:vault_lock",
            title="Locking vault (clearing every stored copy)",
            refresh="vault_status",
        )

    async def _action_vault_seal(self) -> None:
        """Seal the currently resolved passphrase into a systemd-creds credential.

        Defers to sandbox's ``handle_vault_seal`` with the default
        ``--key=auto`` so a TPM2-equipped host gets ``host+tpm2``
        binding automatically.  The sealed-key-mode output streams into
        the log view where the operator can read it.
        """
        self._run_console_action(
            "terok.tui.worker_actions:vault_seal",
            title="Sealing vault passphrase into systemd-creds",
            refresh="vault_status",
        )

    async def _action_vault_to_keyring(self) -> None:
        """Move the currently resolved passphrase into the OS keyring.

        Defers to sandbox's ``handle_vault_to_keyring``: resolves the
        passphrase from whichever tier currently holds it, writes to
        the keyring, flips ``credentials.use_keyring: true``, drops
        any plaintext fallbacks, and removes the session/sealed copies.
        The next container's supervisor resolves the keyring tier afresh.
        The shell-side equivalent of ``terok vault passphrase to-keyring``.
        """
        self._run_console_action(
            "terok.tui.worker_actions:vault_to_keyring",
            title="Moving vault passphrase to OS keyring",
            refresh="vault_status",
        )

    async def _action_vault_change(self) -> None:
        """Change the vault passphrase — re-encrypt the DB, rewrite every tier.

        Delegates to the app-level worker
        ([`TerokTUI._run_vault_change_flow`][terok.tui.app.TerokTUI._run_vault_change_flow]) because the
        conversation (current-passphrase modal when locked → create
        modal → reveal + re-ack) needs ``push_screen_wait``.
        """
        self._run_vault_change_flow()

    async def _action_vault_reveal(self) -> None:
        """Resolve the current passphrase, display it, and offer a save ack.

        Pushes [`VaultRevealModal`][terok.tui.screens.VaultRevealModal]
        with the cleartext + source so the operator can copy the value
        somewhere durable.  The dismissal value (True = "Mark as
        saved", False = explicit decline, None = Esc / close) flows
        through
        [`_on_vault_reveal_result`][terok.tui.project_actions.ProjectActionsMixin._on_vault_reveal_result]
        — the callback pattern (same as
        [`VaultUnlockModal`][terok.tui.screens.VaultUnlockModal]) is
        required because the action runs from
        [`_on_vault_action_result`][terok.tui.app.TerokTUI._on_vault_action_result],
        which is not a worker context — ``push_screen_wait`` would
        raise ``NoActiveWorker`` there.

        Suppressed when the vault is locked — there's nothing to reveal
        without a resolvable passphrase, and the unlock modal is the
        right next step.
        """
        from terok.lib.api import make_sandbox_config
        from terok.lib.api.shield import RecoveryStatus
        from terok.lib.api.vault import NoPassphraseError, WrongPassphraseError

        from .screens import VaultRevealModal

        cfg = make_sandbox_config()
        try:
            passphrase, source = cfg.resolve_passphrase_with_source(prompt_on_tty=False)
        except (NoPassphraseError, WrongPassphraseError) as exc:
            self.notify(
                f"Cannot reveal recovery key: {exc}",
                severity="error",
                timeout=10,
            )
            return
        if not passphrase:
            self.notify(
                "Vault is locked — unlock first (n key), then reveal.",
                severity="warning",
                timeout=8,
            )
            return

        already_acked = RecoveryStatus.is_acknowledged(cfg)
        await self.push_screen(
            VaultRevealModal(passphrase, source or "?", already_acked=already_acked),
            self._on_vault_reveal_result,
        )

    async def _on_vault_reveal_result(self, outcome: object) -> None:
        """Handle [`VaultRevealModal`][terok.tui.screens.VaultRevealModal] dismissal.

        ``outcome`` shape:

        * ``True``  — operator clicked Mark-as-saved; write the marker
          and refresh the pill so the unconfirmed state clears.
        * ``False`` — explicit decline (Close button); nothing to do,
          the pill keeps warning.
        * ``None``  — Esc / already-acked dialog dismissed; no state
          change.
        """
        if outcome is not True:
            return
        from terok.lib.api import make_sandbox_config
        from terok.lib.api.shield import RecoveryStatus

        RecoveryStatus.acknowledge(make_sandbox_config())
        self.notify(
            "Recovery key marked as saved.",
            severity="information",
            timeout=5,
        )
        await self._refresh_vault_status()

    async def _action_vault_acknowledge(self) -> None:
        """Silent ack — mark the current passphrase as saved without re-displaying.

        Counterpart to
        [`_action_vault_reveal`][terok.tui.project_actions.ProjectActionsMixin._action_vault_reveal]
        for the operator who already has the value stashed and just
        wants to clear the pill.  The marker is independent of the
        vault-lock state — acknowledging a locked vault still writes
        the sidecar.
        """
        from terok.lib.api import make_sandbox_config
        from terok.lib.api.shield import RecoveryStatus

        RecoveryStatus.acknowledge(make_sandbox_config())
        self.notify(
            "Recovery key marked as saved.",
            severity="information",
            timeout=5,
        )
        await self._refresh_vault_status()
