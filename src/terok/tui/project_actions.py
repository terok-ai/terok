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
import inspect
import os
import shlex
import sys
import tempfile
import termios
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from terok_util import require_host_tool, write_config_file
from textual import work

from ..lib.api import (
    ProjectConfig,
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
    from .text_screens import EditedText
    from .wizard_screens import ProjectReviewScreen

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


@dataclass(frozen=True)
class InheritCheckbox:
    """The setting in *config_file* that keeps or replaces the instructions below a layer."""

    label: str
    config_file: Path


@dataclass(frozen=True)
class EditableText:
    """A text the operator edits from the TUI, and how it is read, checked and saved."""

    title: str
    """Names the text on the editor's border and in notifications."""
    draft_name: str
    """File name of the ``$EDITOR`` draft — its suffix picks the editor's syntax mode."""
    load: Callable[[], str]
    save: Callable[[str], object]
    validate: Callable[[str], str | None] | None = None
    """Returns why a text must not be saved, or ``None`` when it may be."""
    language: str | None = None
    """Syntax highlighting in the integrated editor; ``None`` is plain text."""
    reference: Callable[[], str] | None = None
    """Returns the Markdown options reference the integrated editor shows for a config file."""
    inherit: InheritCheckbox | None = None
    """The inherit setting the integrated editor shows next to an instructions text."""

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        title: str,
        validate: Callable[[str], str | None] | None = None,
        language: str | None = None,
        reference: Callable[[], str] | None = None,
        inherit: InheritCheckbox | None = None,
    ) -> EditableText:
        """A text stored in the file at *path* — empty until the first save creates the file."""
        return cls(
            title=title,
            draft_name=path.name,
            load=lambda: path.read_text(encoding="utf-8") if path.is_file() else "",
            save=partial(write_config_file, path),
            validate=validate,
            language=language,
            reference=reference,
            inherit=inherit,
        )


def _inherit_setting(text: EditableText) -> bool | None:
    """Return the inherit setting *text*'s editor shows, or ``None`` when it shows none."""
    from ..lib.api.agents import inherits_instructions

    return inherits_instructions(text.inherit.config_file) if text.inherit else None


def _save_inherit_setting(text: EditableText, inherit: bool | None) -> None:
    """Write the inherit setting from *text*'s editor to its config file."""
    from ..lib.api.agents import set_instructions_inherit

    if text.inherit is not None and inherit is not None:
        set_instructions_inherit(text.inherit.config_file, inherit)


def _read_draft(
    draft: Path, original: str, validate: Callable[[str], str | None] | None
) -> tuple[str, str | None]:
    """Read an edited ``$EDITOR`` draft; return its text and why it must not be saved, or ``None``.

    An unchanged draft passes unchecked — quitting without a change is
    no edit, even of a text that does not load.
    """
    try:
        edited = draft.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return original, f"The edited file is not valid UTF-8: {exc}"
    if edited == original or validate is None:
        return edited, None
    return edited, validate(edited)


def _snippet_texts(project: ProjectConfig) -> list[EditableText]:
    """Return the Docker snippets *project*'s image build includes, as editable texts.

    The file ``image.user_snippet_file`` names comes first, as in the
    build.  The inline ``image.user_snippet_inline`` follows when it
    holds text — and stands alone when no file is configured, so a
    project without any snippet starts an inline one.
    """
    from ..lib.api import set_project_snippet_inline

    snippets: list[EditableText] = []
    if (path := project.snippet_path) is not None:
        snippets.append(
            EditableText.from_file(path, title=f"Docker snippet — {project.name} ({path.name})")
        )
    inline = project.snippet_inline or ""
    if inline.strip() or not snippets:
        snippets.append(
            EditableText(
                title=f"Docker snippet — {project.name} (inline in project.yml)",
                draft_name="snippet.dockerfile",
                load=lambda: inline,
                save=partial(set_project_snippet_inline, project.name),
            )
        )
    return snippets


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
        def _offer_restart_for_config(self) -> None: ...
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
        login containers and the external editor.  The child runs as an
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
        """Initialize SSH, offering an editable comment before creating a key."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        from ..lib.api import get_project
        from .ssh_key_screens import SshKeyCommentScreen

        pid = self.current_project_name
        try:
            suggestion = await asyncio.to_thread(get_project(pid).suggested_ssh_key_comment)
        except Exception as exc:  # noqa: BLE001 - vault failures are operator-facing
            self.notify(f"SSH key unavailable: {exc}", severity="error")
            return

        def initialize(comment: str | None) -> None:
            """Dispatch initialization with the operator's chosen comment."""
            self._run_console_action(
                "terok.tui.worker_actions:init_ssh",
                pid,
                comment,
                title=f"Initializing SSH key for {pid}",
            )

        def confirmed(comment: str | None) -> None:
            """Cancel without dispatch, or resolve an unchanged suggestion at creation time."""
            if comment is not None:
                initialize(None if comment == suggestion else comment)

        if suggestion is None:
            initialize(None)
        else:
            self.push_screen(SshKeyCommentScreen(suggestion, title=f"SSH key for {pid}"), confirmed)

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
        always selected. The top-level "Authenticate providers"
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
        prompt always saw EOF.  The login path reuses the same
        tmux/terminal/suspend cascade as project shell logins.
        """
        from ..lib.api import available_auth_modes, load_auth_providers
        from .screens import AuthModeScreen

        if provider not in load_auth_providers():
            self.notify(f"Unknown auth provider: {provider}", severity="error")
            return

        modes = available_auth_modes(provider)
        if not modes:
            self.notify(
                f"Auth for {provider!r} needs the vendor login, which is disabled "
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
            # "login" runs the vendor login; "device_auth" runs the same
            # login headlessly via a device code.
            await self._auth_via_login(
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
        except Exception as exc:  # noqa: BLE001 - surface every storage failure
            self.notify(
                f"Failed to store API key for {provider}: {exc}",
                severity="error",
                timeout=10,
            )
            return
        self.notify(f"API key stored for {provider}.")

    async def _auth_via_login(
        self, provider: str, *, project_name: str | None, device_auth: bool = False
    ) -> None:
        """Prepare a login container session and hand it to the launcher.

        With *device_auth* the prepared session runs the provider's headless
        device-code login instead of the normal flow.
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
                    f"No agent image found for the {provider} login.  Run "
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
        # ``prepare_login`` raises ``SystemExit`` only for an unknown or
        # login-less provider — both already excluded by ``_run_auth_flow``
        # before we get here, so no defensive catch is needed.  An
        # unexpected raise surfaces through the ``@work`` harness instead
        # of tearing down the app.
        session = Authenticator(provider).prepare_login(
            project_name,
            mounts_dir=mounts_dir,
            image=image,
            expose_token=expose,
            credential_set=credential_set,
            device_auth=device_auth,
        )
        await self._launch_login_container(session)

    async def _launch_login_container(self, session: AuthSession) -> None:
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
                "The vendor login needs a host terminal — unavailable in the web TUI.  "
                "Use API-key auth instead, or run `terok auth` on the host.",
                severity="error",
                timeout=12,
            )
            session.cleanup()
            return

        method, _port = launch_login(session.argv, title=session.title)
        if method in ("tmux", "terminal"):
            self.notify(
                f"Login container started in new {method}.  Credentials "
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
                executable=require_host_tool("podman"),
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
            executable=require_host_tool("podman"),
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
                    f"Login container exited {exit_code} — credentials not captured.",
                    severity="warning",
                    timeout=10,
                )
                return
            session.capture()
            self.notify(f"Credentials captured for {session.provider.name}.")
        except Exception as exc:  # noqa: BLE001 - surface any extractor / vault error
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
        """Sync gate (init if doesn't exist, sync if exists).

        The sync worker only ever applies safe changes; once it finishes,
        any destructive changes it left pending are offered for explicit
        confirmation in a modal — that is the *only* path that deletes or
        force-moves gate branches.
        """
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name

        def _offer_pending_review() -> None:
            self.run_worker(self._review_pending_gate_ops(pid))

        self._run_console_action(
            "terok.tui.worker_actions:sync_gate",
            pid,
            title=f"Syncing gate for {pid}",
            on_complete=_offer_pending_review,
        )

    async def _review_pending_gate_ops(self, pid: str) -> None:
        """Offer the confirmation modal for the gate's pending destructive ops.

        Reads the pending set fresh from the gate (offline — no fetch, no
        SSH) rather than trusting anything cached: the ops shown are the
        ops applied, and their compare-and-swap guards still protect
        against agent pushes racing the modal.
        """
        from ..lib.api import load_project, make_git_gate
        from ..lib.api.gate import describe_pending_op

        try:
            pending = await asyncio.to_thread(
                lambda: make_git_gate(load_project(pid)).pending_ops()
            )
        except (Exception, SystemExit):  # noqa: BLE001 - sync already reported errors
            return
        if not pending or pid != self.current_project_name:
            return

        lines = [
            "Upstream wants branch changes the sync would not apply on its own:",
            "",
            *(f"  • {describe_pending_op(op)}" for op in pending),
            "",
            "Old tips are saved as backup refs before anything is changed",
            "(recoverable via `terok project gate-backups`).",
        ]

        def _on_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self._run_console_action(
                "terok.tui.worker_actions:apply_gate_ops",
                pid,
                [dict(op) for op in pending],
                title=f"Applying gate changes for {pid}",
            )

        from .screens import ConfirmDestructiveScreen

        await self.push_screen(
            ConfirmDestructiveScreen(
                message="\n".join(lines),
                title=f"Pending gate changes: {pid}",
                confirm_label="Apply",
            ),
            _on_confirm,
        )

    async def _action_gate_backups(self) -> None:
        """Browse the gate's backups; restore or delete the chosen one.

        The list is read fresh and offline (no fetch) so it reflects the
        gate as it is now; the restore/delete then runs as a captured
        child action, and the gate's own compare-and-swap guards protect
        a restore against an agent push that raced the modal.
        """
        import asyncio

        from ..lib.api import load_project, make_git_gate
        from .screens import ConfirmDestructiveScreen, GateBackupsScreen

        if not self.current_project_name:
            self.notify("No project selected.")
            return
        pid = self.current_project_name
        try:
            backups = await asyncio.to_thread(
                lambda: make_git_gate(load_project(pid)).list_backups()
            )
        except (Exception, SystemExit):  # noqa: BLE001 - a bad gate just means nothing to show
            self.notify("Could not read gate backups.")
            return
        if pid != self.current_project_name:
            return

        def _on_pick(picked: tuple[str, str] | None) -> None:
            """Confirm the chosen restore/delete, then dispatch it (callback form).

            Nested callback-style ``push_screen`` — not ``push_screen_wait``
            — because this runs as a screen-result callback, outside any
            worker.
            """
            if picked is None:
                return
            action, ref = picked
            if action == "restore":
                confirm = ConfirmDestructiveScreen(
                    message=(
                        f"Restore a branch to this backup?\n\n  {ref}\n\n"
                        "The tip it replaces is backed up first, so this can be undone."
                    ),
                    title=f"Restore gate backup: {pid}",
                    confirm_label="Restore",
                )
                worker_ref = "terok.tui.worker_actions:restore_gate_backup"
                title = f"Restoring gate backup for {pid}"
            else:
                confirm = ConfirmDestructiveScreen(
                    message=f"Delete this backup ref permanently?\n\n  {ref}",
                    title=f"Delete gate backup: {pid}",
                    confirm_label="Delete",
                )
                worker_ref = "terok.tui.worker_actions:delete_gate_backup"
                title = f"Deleting gate backup for {pid}"

            def _on_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self._run_console_action(worker_ref, pid, ref, title=title)

            self.push_screen(confirm, _on_confirm)

        await self.push_screen(GateBackupsScreen(backups), _on_pick)

    # ---------- Text editing (instructions, project.yml, config.yml, snippets) ----------

    def _edit_text(
        self,
        text: EditableText,
        *,
        done_msg: str,
        after_save: Callable[[], object] | None = None,
    ) -> None:
        """Edit *text* in the integrated editor or ``$EDITOR``, and save an accepted change.

        The text opens in the integrated
        [`TextEditorScreen`][terok.tui.text_screens.TextEditorScreen],
        which offers ``$EDITOR`` on a button.  With the
        ``tui.external_editor`` preference on, it goes straight to
        ``$EDITOR`` — as long as ``$EDITOR`` is set and the TUI is *not*
        web-served, as the editor takes over the terminal and a web TUI
        has none.  Both ways, nothing is written until the text's
        validator accepts it, and a failed write keeps the edit.
        *after_save* runs after a save; it may return an awaitable.
        """
        self._run_text_edit(text, done_msg, after_save)

    @work(group="text-edit", exit_on_error=False)
    async def _run_text_edit(
        self, text: EditableText, done_msg: str, after_save: Callable[[], object] | None
    ) -> None:
        """Worker body of ``_edit_text`` — a failure becomes a notification, never a crash."""
        try:
            await self._edit_and_save(text, done_msg=done_msg, after_save=after_save)
        except Exception as exc:  # noqa: BLE001 - an edit must never take the TUI down
            self.notify(f"Editing {text.title} failed: {exc}", severity="error", markup=False)

    async def _edit_and_save(
        self, text: EditableText, *, done_msg: str, after_save: Callable[[], object] | None
    ) -> None:
        """Load *text*, let the operator edit it, and save the edit when it changed anything.

        A write that fails sends the operator back to the edit, with the
        reason, until it is saved or abandoned.
        """
        try:
            original = text.load()
        except (OSError, UnicodeDecodeError) as exc:
            self.notify(f"Cannot read {text.title}: {exc}", severity="error", markup=False)
            return
        from .text_screens import EditedText

        editor = self._external_editor()
        draft = EditedText(original, None if editor else _inherit_setting(text))
        saved_setting = draft.checked
        while True:
            edited = await self._edit_once(text, draft, editor)
            if edited is None:
                return
            if edited.text == original and edited.checked == saved_setting:
                self.notify("No changes to save.")
                return
            try:
                if edited.text != original:
                    text.save(edited.text)
                    original = edited.text
                if edited.checked != saved_setting:
                    _save_inherit_setting(text, edited.checked)
                    saved_setting = edited.checked
                break
            except (OSError, SystemExit, ValueError) as exc:
                reason = f"Cannot save {text.title}: {exc}"
            draft = edited
            if editor is None:
                self.notify(reason, severity="error", markup=False)
            elif await self._confirm_discard(text.title, reason):
                return
        self.notify(done_msg, markup=False)
        if after_save is not None and inspect.isawaitable(pending := after_save()):
            await pending

    async def _edit_once(
        self, text: EditableText, draft: EditedText, editor: str | None
    ) -> EditedText | None:
        """Edit *draft* once, in *editor* or the integrated editor; ``None`` when abandoned."""
        from .text_screens import EditedText, TextEditorScreen

        if editor is not None:
            edited = await self._edit_in_external_editor(
                editor,
                draft.text,
                draft_name=text.draft_name,
                title=text.title,
                validate=text.validate,
            )
            return None if edited is None else EditedText(edited)

        show_checkbox = draft.checked is not None
        return await self.push_screen_wait(
            TextEditorScreen(
                draft.text,
                title=text.title,
                language=text.language,
                validate=text.validate,
                reference=text.reference,
                edit_externally=self._external_edit_hook(
                    draft_name=text.draft_name, title=text.title, validate=text.validate
                ),
                checkbox_label=text.inherit.label if text.inherit and show_checkbox else None,
                checked=bool(draft.checked),
            )
        )

    def _available_editor(self) -> str | None:
        """Return the ``$EDITOR`` command where a terminal can run it, or ``None``."""
        if self.is_web:
            return None
        return os.environ.get("EDITOR", "").strip() or None

    def _external_editor(self) -> str | None:
        """Return the ``$EDITOR`` command that edits go straight to, or ``None``."""
        from ..lib.api import get_config

        return self._available_editor() if get_config().tui_external_editor else None

    async def _edit_in_external_editor(
        self,
        editor: str,
        original: str,
        *,
        draft_name: str,
        title: str,
        validate: Callable[[str], str | None] | None,
    ) -> str | None:
        """Edit *original* as a draft file in *editor*; return the accepted text, or ``None``.

        The draft is *draft_name* in a private temporary directory: the
        file name gives the editor its syntax mode, and the real file
        changes only after the edited text passes *validate*.  An editor
        that exits non-zero abandons the edit; an unchanged draft comes
        back as it is.  A refused text shows why and goes back to the
        editor, unless the operator discards it.  Local-terminal only —
        ``_available_editor`` never offers ``$EDITOR`` under
        ``App.is_web``, so the ``suspend()`` always has a real terminal
        to suspend *to*.
        """
        with tempfile.TemporaryDirectory(prefix="terok-edit-") as draft_dir:
            draft = Path(draft_dir) / draft_name
            draft.write_text(original, encoding="utf-8")
            while True:
                code = await self._run_suspended(
                    *shlex.split(editor), str(draft), prompt_on_success=False
                )
                if code != 0:
                    return None
                edited, verdict = _read_draft(draft, original, validate)
                if verdict is None:
                    return edited
                if await self._confirm_discard(title, verdict):
                    return None

    async def _confirm_discard(self, title: str, verdict: str) -> bool:
        """Show why an edit of *title* was refused; return whether the operator discards it."""
        from .screens import ConfirmDestructiveScreen

        # push_screen_wait is typed Screen[object]; Screen is invariant, so
        # Screen[bool] does not satisfy it (a stubs issue).
        confirm: Any = ConfirmDestructiveScreen(
            message=f"{verdict}\n\nDiscard your changes, or Cancel to go back to them in the editor.",
            title=f"Not saved — {title}",
            confirm_label="Discard",
        )
        return bool(await self.push_screen_wait(confirm))

    async def _action_edit_instructions(self) -> None:
        """Edit the current project's instructions.md."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        from .text_screens import MARKDOWN

        pid = self.current_project_name
        try:
            root = load_project(pid).root
        except SystemExit as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        self._edit_text(
            EditableText.from_file(
                root / "instructions.md",
                title=f"Instructions — {pid}",
                language=MARKDOWN,
                inherit=InheritCheckbox(
                    "Include the global and bundled default instructions", root / "project.yml"
                ),
            ),
            done_msg=f"Instructions updated for {pid}",
            after_save=self._refresh_project_state,
        )

    async def _action_edit_project_yml(self) -> None:
        """Edit the current project's project.yml."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        self._edit_project_yml(self.current_project_name)

    def _edit_project_yml(self, project_name: str) -> None:
        """Edit *project_name*'s project.yml; a save that would break the project is refused.

        Also the repair path for a broken project: a valid save re-scans
        the project list, which turns the project healthy again.
        """
        from ..lib.api import project_config_path, project_config_reference, validate_project_yaml

        try:
            path = project_config_path(project_name)
        except SystemExit as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        self._edit_text(
            EditableText.from_file(
                path,
                title=f"project.yml — {project_name}",
                validate=partial(validate_project_yaml, project_name),
                language="yaml",
                reference=project_config_reference,
            ),
            done_msg=f"Saved {path}",
            after_save=self.refresh_projects,
        )

    async def _action_edit_snippet(self) -> None:
        """Edit the current project's Docker snippet — the text its image build includes.

        The snippet lives where ``project.yml`` says: the file that
        ``image.user_snippet_file`` names, the inline
        ``image.user_snippet_inline``, or both — then a chooser asks
        which one to edit.  A project without a snippet starts an inline
        one.
        """
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        try:
            project = load_project(self.current_project_name)
        except SystemExit as exc:
            self.notify(str(exc), severity="error", markup=False)
            return
        snippets = _snippet_texts(project)
        if len(snippets) == 1:
            self._edit_snippet(snippets[0])
            return

        from .screens import ChoiceScreen

        def _on_pick(choice: str | None) -> None:
            """Edit the picked snippet; ``None`` (Escape) edits nothing."""
            if choice is not None:
                self._edit_snippet(snippets[int(choice)])

        self.push_screen(
            ChoiceScreen(
                f"Docker snippets — {project.name}",
                [(str(index), snippet.title) for index, snippet in enumerate(snippets)],
            ),
            _on_pick,
        )

    def _edit_snippet(self, snippet: EditableText) -> None:
        """Edit one Docker snippet; the image build picks it up at the next build."""
        self._edit_text(
            snippet,
            done_msg=f"Saved {snippet.title}.  Rebuild the project image to apply it.",
            after_save=self.refresh_projects,
        )

    async def _action_edit_global_config(self) -> None:
        """Edit the user config file; a valid save offers the restart that applies it."""
        from ..lib.api import get_config, global_config_reference, validate_global_config

        path = get_config().global_config_path
        self._edit_text(
            EditableText.from_file(
                path,
                title=f"Global config — {path}",
                validate=validate_global_config,
                language="yaml",
                reference=global_config_reference,
            ),
            done_msg=f"Saved {path}",
            after_save=self._offer_restart_for_config,
        )

    async def _action_toggle_instructions_inherit(self) -> None:
        """Toggle the project's instructions between inherit and override."""
        if not self.current_project_name:
            self.notify("No project selected.")
            return
        from ..lib.api import project_config_path
        from ..lib.api.agents import inherits_instructions, set_instructions_inherit

        try:
            project_yml = project_config_path(self.current_project_name)
            if not project_yml.is_file():
                self.notify("No project.yml found.")
                return
            inherit = inherits_instructions(project_yml)
            if inherit is None:
                self.notify(
                    "Toggle supports list/implicit instructions only; "
                    "edit project.yml manually for this form.",
                    severity="warning",
                )
                return
            set_instructions_inherit(project_yml, not inherit)
            mode = "custom only (defaults disabled)" if inherit else "inheriting defaults"
            self.notify(f"Instructions: {mode}")
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
        from .text_screens import MARKDOWN, TextViewScreen

        project = load_project(pid)
        effective = resolve_agent_config(
            pid, agent_config=project.agent_config, project_root=project.root
        )
        provider = get_agent(None, default_agent=project.default_agent)
        text = resolve_instructions(
            effective, provider.name, project_root=project.root, family=project.known_family
        )
        await self.push_screen(
            TextViewScreen(text, title=f"Resolved instructions — {pid}", language=MARKDOWN)
        )

    async def _action_show_instructions(self) -> None:
        """Open the screen for the instructions every project starts from."""
        from .instructions_screen import InstructionsScreen

        await self.push_screen(InstructionsScreen(), self._on_instructions_choice)

    async def _on_instructions_choice(self, choice: str | None) -> None:
        """Run the action picked on the instructions screen."""
        from .instructions_screen import EDIT_GLOBAL, SHOW_DEFAULT

        if choice == EDIT_GLOBAL:
            await self._action_edit_global_instructions()
        elif choice == SHOW_DEFAULT:
            await self._action_show_default_instructions()

    async def _action_edit_global_instructions(self) -> None:
        """Edit the global instructions.md."""
        from ..lib.api import get_config
        from ..lib.api.agents import global_instructions_path
        from .text_screens import MARKDOWN

        self._edit_text(
            EditableText.from_file(
                global_instructions_path(),
                title="Global instructions",
                language=MARKDOWN,
                inherit=InheritCheckbox(
                    "Include the bundled default instructions", get_config().global_config_path
                ),
            ),
            done_msg="Global instructions updated",
            after_save=self._refresh_project_state,
        )

    async def _action_show_default_instructions(self) -> None:
        """Display the bundled default instructions (read-only)."""
        from terok.lib.api.agents import bundled_default_instructions

        from .text_screens import MARKDOWN, TextViewScreen

        text = bundled_default_instructions()
        await self.push_screen(
            TextViewScreen(text, title="Bundled default instructions", language=MARKDOWN)
        )

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
        except Exception as exc:  # noqa: BLE001 - last-resort wizard error surface
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
            WizardFormScreen,
        )

        values: dict[str, str] | None = None
        while True:
            values = await self.push_screen_wait(
                WizardFormScreen(
                    initial=values,
                    edit_externally=self._external_edit_hook(
                        draft_name="snippet.dockerfile", title="Docker snippet"
                    ),
                )
            )
            if values is None:
                return  # user cancelled the form

            rendered = render_project_yaml(values)
            review_result = await self.push_screen_wait(
                self._project_review_screen(values["project_name"], rendered)
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

    def _project_review_screen(self, project_name: str, rendered: str) -> ProjectReviewScreen:
        """Build the wizard's review step for *rendered*, editable in ``$EDITOR`` where edits go there.

        Initialize is refused for a text that would not load as the new
        project — the check every other ``project.yml`` editor applies.
        """
        from ..lib.api import project_config_reference, validate_project_yaml
        from .wizard_screens import ProjectReviewScreen

        validate = partial(validate_project_yaml, project_name)
        return ProjectReviewScreen(
            project_name,
            rendered,
            validate=validate,
            reference=project_config_reference,
            edit_externally=self._external_edit_hook(
                draft_name="project.yml", title=f"project.yml — {project_name}", validate=validate
            ),
        )

    def _external_edit_hook(
        self,
        *,
        draft_name: str,
        title: str,
        validate: Callable[[str], str | None] | None = None,
    ) -> Callable[[str], Awaitable[str | None]] | None:
        """Return the ``$EDITOR`` round-trip for a text, or ``None`` where no editor can run."""
        editor = self._available_editor()
        if editor is None:
            return None
        return partial(
            self._edit_in_external_editor,
            editor,
            draft_name=draft_name,
            title=title,
            validate=validate,
        )

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
        except Exception:  # noqa: BLE001 - an advisory probe that must not surface
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
        """Prompt for the SQLCipher passphrase and store it in the session cache.

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
        except Exception:  # noqa: BLE001 - fall through to the unlock prompt on a broken probe
            state = VaultState.LOCKED
        if state is VaultState.UNPROVISIONED:
            self._run_vault_provision_flow()
            return
        await self.push_screen(VaultUnlockModal(), self._on_vault_unlock_result)

    async def _action_vault_lock(self) -> None:
        """Lock the vault — clear every stored copy of the passphrase.

        Locking removes the temporary cache and saved desktop keyring /
        systemd-creds copies, and disconnects passphrase-command: against a
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
                    "kernel keyring / tmpfs session cache, desktop keyring entry, "
                    "and sealed systemd-creds credential. It also disconnects "
                    "passphrase-command without deleting the helper's secret.\n\n"
                    "Running services stay open; this does not revoke their access.\n\n"
                    "You will need your saved passphrase to unlock again. If you "
                    "have not saved it off-host, the vault becomes unrecoverable."
                ),
                title="Lock vault — delete saved passphrases",
                confirm_label="Delete saved passphrases",
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

    async def _action_vault_to_desktop_keyring(self) -> None:
        """Move the currently resolved passphrase into the desktop keyring.

        Defers to sandbox's ``handle_vault_to_desktop_keyring``: validates and
        stores the passphrase, verifies desktop-keyring readback, then
        enables that tier and removes the prior copies and helper wiring.
        The next container's supervisor resolves the desktop keyring afresh.
        The shell-side equivalent of ``terok vault passphrase to-desktop-keyring``.
        """
        self._run_console_action(
            "terok.tui.worker_actions:vault_to_desktop_keyring",
            title="Moving vault passphrase to desktop keyring",
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
