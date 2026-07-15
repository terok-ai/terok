#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terok TUI application built on Textual."""

import getpass
import inspect
import os
import socket
import sys
from collections.abc import Iterator
from typing import Any, Literal


def enable_pycharm_debugger() -> None:
    """Attach the PyCharm remote debugger when PYCHARM_DEBUG is set."""
    import os

    if os.getenv("PYCHARM_DEBUG"):
        import pydevd_pycharm

        pydevd_pycharm.settrace(
            host="localhost",
            port=5678,
            suspend=False,  # or True if you want it to break immediately
        )


# Try to detect whether 'textual' is available. We avoid importing it or the
# widgets module at import time so the package can be installed without the
# optional TUI dependencies.
try:  # pragma: no cover - simple availability probe
    import importlib.util

    _HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
except Exception:  # pragma: no cover - textual not installed
    _HAS_TEXTUAL = False


if _HAS_TEXTUAL:
    # Import textual and our widgets only when available
    from dataclasses import dataclass

    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.css.query import NoMatches
    from textual.theme import Theme
    from textual.timer import Timer
    from textual.widgets import Footer, Header
    from textual.worker import Worker, WorkerState

    from terok.lib.api import SandboxConfig
    from terok.lib.api.gate import GateStalenessInfo
    from terok.lib.api.setup import (
        EnvironmentCheck,
        SetupVerdict,
        check_environment as _shield_check_environment,
        needs_setup,
    )
    from terok.lib.api.shield import RecoveryStatus, ShieldManager
    from terok.lib.api.vault import VaultStatus

    from ..lib.api import (
        BrokenProject,
        Config,
        ContainerEventStream,
        Project,
        ProjectConfig,
        Task,
        container_name,
        discover_projects,
        execute_panic,
        format_panic_report,
        get_config,
        get_tasks,
        load_project,
        make_git_gate,
        panic_stop_containers,
        save_tui_theme,
        set_experimental,
    )

    # Import version info function (shared with CLI --version)
    from ..lib.core.version import (
        get_version_info as _get_version_info,
        installed_dist_version as _installed_dist_version,
        short_version as _short_version,
    )
    from ..lib.util.yaml import YAMLError
    from . import tmux_session

    # Exit code 5 is the ``terok setup`` partial-success signal — every
    # install phase succeeded but a manual step (currently: SELinux
    # policy install on socket-mode hosts) is still required.  The
    # value is owned by the sandbox setup CLI; the TUI mirrors it here
    # so the post-run dispatch can branch into ``_offer_selinux_fix``.
    _EXIT_MANUAL_STEP_NEEDED = 5

    # ``App.run()`` result asking ``_run_tui`` to re-exec the process in
    # place — used when the operator accepts the restart offer after a
    # terok upgrade landed on disk.
    _RESTART_EXIT_RESULT = "terok-restart"

    # How often to compare the on-disk terok version against the running
    # one when nothing else triggers a probe.  Focus-in (re-attaching the
    # tmux session, switching back to the TUI's window) probes immediately
    # — that's the "operator just came back" moment the offer should meet
    # — so the interval only covers terminals that don't report focus, and
    # can afford to be lazy.
    _UPDATE_CHECK_INTERVAL_S = 600.0

    @dataclass(frozen=True)
    class ProjectStateResult:
        """Result of loading project infrastructure state in a background thread."""

        project_name: str
        project: ProjectConfig | None = None
        state: dict | None = None
        staleness: GateStalenessInfo | None = None
        shield_env: EnvironmentCheck | None = None
        error: str | None = None

    from .askpass_service import AskpassService
    from .clipboard import get_clipboard_helper_status
    from .console_log import ConsoleLogMixin, ConsoleLogRegistry
    from .polling import PollingMixin
    from .project_actions import ProjectActionsMixin
    from .screens import (
        ConfirmDestructiveScreen,
        ProjectDetailsScreen,
        QuitConfirmScreen,
        ShieldScreen,
        TaskDetailsScreen,
        TmuxQuitScreen,
        UpdateRestartScreen,
        VaultScreen,
        VaultUnlockModal,
    )
    from .setup_screen import SetupOutcome, SetupScreen
    from .task_actions import TaskActionsMixin
    from .task_watcher import TaskWatcher
    from .widgets import (
        PanicButton,
        ProjectList,
        ProjectState,
        StatusBar,
        TaskDetails,
        TaskList,
        TaskMeta,
    )
    from .worker_log_screen import WorkerLogScreen

    # -- Dispatch tables mapping action IDs to handler method names ----------
    # These are the single source of truth for action routing.  Both
    # _handle_project_action and _handle_task_action do a dict lookup here
    # instead of maintaining long if/elif chains.

    PROJECT_ACTION_HANDLERS: dict[str, str] = {
        "project_init": "_action_project_init",
        "generate": "action_generate_dockerfiles",
        "build": "action_build_images",
        "build_agents": "_action_build_agents",
        "build_full": "_action_build_full",
        "init_ssh": "action_init_ssh",
        "sync_gate": "_action_sync_gate",
        "edit_instructions": "_action_edit_instructions",
        "toggle_inherit": "_action_toggle_instructions_inherit",
        "show_resolved": "_action_show_resolved_instructions",
        "import_opencode_config": "_action_import_opencode_config",
        "delete_project": "_action_delete_project",
    }

    SHIELD_ACTION_HANDLERS: dict[str, str] = {
        "shield_setup": "_action_shield_setup",
    }

    VAULT_ACTION_HANDLERS: dict[str, str] = {
        "vault_unlock": "_action_vault_unlock",
        "vault_lock": "_action_vault_lock",
        "vault_seal": "_action_vault_seal",
        "vault_to_keyring": "_action_vault_to_keyring",
        "vault_reveal": "_action_vault_reveal",
        "vault_acknowledge": "_action_vault_acknowledge",
        "vault_change": "_action_vault_change",
    }

    TASK_ACTION_HANDLERS: dict[str, str] = {
        "task_start_cli": "_action_task_start_cli",
        "task_start_toad": "_action_task_start_toad",
        "task_start_unattended": "_action_task_start_unattended",
        "delete": "action_delete_task",
        "restart": "_action_restart_task",
        "recreate": "_action_recreate_task",
        "followup": "_action_task_followup",
        "diff_head": "action_copy_diff_head",
        "diff_prev": "action_copy_diff_prev",
        "login": "_action_login",
        "follow_logs": "_action_follow_logs",
        "rename": "_action_rename_task",
        "stop": "_action_stop_task",
        "shield_down": "_action_shield_down",
        "shield_disengaged": "_action_shield_disengaged",
        "shield_up": "_action_shield_up",
        "shield_interactive": "_action_shield_interactive",
        "shield_watch": "_action_shield_watch",
        "show_clearance": "action_show_clearance",
    }

    class TerokTUI(PollingMixin, ProjectActionsMixin, TaskActionsMixin, ConsoleLogMixin, App):
        """Redesigned TUI frontend for terok core modules."""

        CSS_PATH = None

        # Layout rules for the new streamlined design with borders
        CSS = """
        Screen {
            layout: vertical;
            background: $background;
        }

        #main {
            height: 1fr;
            background: $background;
        }

        /* Main container borders */
        #left-pane {
            width: 1fr;
            padding: 1;
            background: $background;
        }

        #right-pane {
            width: 1fr;
            padding: 1;
            background: $background;
        }

        /* Projects section with embedded title */
        #project-list {
            border: round $primary;
            border-title-align: right;
            background: $surface;
            height: auto;
            max-height: 8;
        }

        /* Project details section */
        #project-state {
            border: round $primary;
            border-title-align: right;
            background: $background;
            height: 1fr;
            min-height: 5;
            margin-top: 1;
        }

        /* Tasks section with embedded title */
        #task-list {
            border: round $primary;
            border-title-align: right;
            background: $surface;
            height: auto;
            max-height: 8;
        }

        /* Task details section */
        #task-details {
            border: round $primary;
            border-title-align: right;
            background: $background;
            height: 1fr;
            min-height: 5;
            margin-top: 1;
        }

        /* Task details internal layout */
        #task-details-content {
            height: 1fr;
        }
        """

        BINDINGS = [
            ("q", "confirm_quit", "Quit"),
            ("a", "authenticate", "Auth"),
            ("P", "panic", "PANIC"),
            # Vim-style navigation, deliberately hidden from the footer (the
            # arrow keys remain the discoverable path).  These are global
            # because every list/menu in the app — the two main panes plus the
            # modal OptionList/RadioSet menus — answers to ``cursor_up`` /
            # ``cursor_down``, so one set of bindings covers them all.  A
            # focused text widget swallows the keystroke before the binding
            # fires, so typing "j" in a prompt still types "j".
            Binding("j", "vim_down", "Down", show=False),
            Binding("k", "vim_up", "Up", show=False),
            Binding("h", "vim_left", "Left", show=False),
            Binding("l", "vim_right", "Right", show=False),
        ]

        def __init__(self) -> None:
            """Initialize the TUI, setting up internal state and dynamic title."""
            super().__init__()
            # Console panes show CLI/build output authored for dark
            # terminals; keep the dark ANSI palette in light themes too
            # so AnsiLog's palette/background pairing holds everywhere.
            self.ansi_theme_light = self.ansi_theme_dark
            # Snapshot the global config once; reuse via ``self._config``.
            self._config: Config = get_config()
            # Set dynamic title with version and branch info
            self._update_title()

            # Session-scoped registry of dispatched-action console logs
            # (image builds, gate/vault ops, container starts).  In-memory
            # only — forgotten when the app closes.
            self.console_logs = ConsoleLogRegistry()

            self.current_project_name: str | None = None
            self.current_task: TaskMeta | None = None
            self._projects_by_id: dict[str, ProjectConfig] = {}
            self._broken_by_id: dict[str, BrokenProject] = {}
            # Tracks which broken-project names have already been toasted so
            # repeated ``refresh_projects`` calls don't re-notify the same
            # set on every action (#565).  Resets when all breakages clear,
            # so a later regression toasts again.
            self._announced_broken_ids: set[str] = set()
            self._last_task_count: int | None = None
            # Upstream polling state
            self._staleness_info: GateStalenessInfo | None = None
            self._polling_timer = None
            self._polling_project_name: str | None = None  # Project name the timer was started for
            self._last_notified_stale: bool = False  # Track if we already notified about staleness
            self._auto_sync_cooldown: dict[str, float] = {}  # Per-project cooldown timestamps
            # Container status tracking: inotify watch + podman event stream,
            # with a slow resync timer as insurance.
            self._container_status_timer = None
            # Last successfully queried container states per project.  Seeds
            # freshly loaded task rows and carries the display through polls
            # where podman doesn't answer (e.g. locked by a concurrent image
            # build) — only a successful query may overwrite a state (#1134).
            self._last_container_states: dict[str, dict[str, str | None]] = {}
            self._task_watcher: TaskWatcher | None = None
            self._container_event_stream: ContainerEventStream | None = None
            self._watch_debounce = None
            self._last_shield_env: EnvironmentCheck | None = None
            self._last_vault_status: VaultStatus | None = None
            self._vault_watcher: TaskWatcher | None = None
            self._vault_watch_debounce: Timer | None = None
            # Cached state for detail screens
            self._last_project_state: dict | None = None
            self._last_image_old: bool | None = None
            # Selection persistence
            self._last_selected_project: str | None = None
            self._last_selected_tasks: dict[str, str] = {}  # project_name -> task_id
            # First-run nudge marker — flipped when the wizard has auto-opened
            # once, so subsequent empty-install starts don't nag.
            self._first_run_dismissed: bool = False
            # Lazy — only instantiated + bound when a ``ssh.use_personal: true``
            # project actually spawns a subprocess.  Users who don't opt in
            # never bind the socket.
            self._askpass_service: AskpassService | None = None
            # Tasks whose CLI/Toad launch worker is in flight; drives the
            # ⏳ badge via ``TaskMeta.starting``.
            self._launching_tasks: set[tuple[str, str]] = set()
            # Tasks whose delete worker is in flight *this session*.  The
            # on-disk ``deleting`` flag persists across restarts (and survives
            # a crash mid-delete); this set tracks only live workers, so the
            # delete guard can tell an active teardown apart from a stale flag
            # left by an interrupted session.
            self._deleting_tasks: set[tuple[str, str]] = set()
            # On-disk version already offered as a restart; a dismissed
            # offer is not repeated until yet another version lands.
            self._update_offered_version: str | None = None

        def _update_title(self) -> None:
            """Update the TUI title with version and branch information.

            Right-aligned ``sub_title`` carries ``user@host`` so a TUI
            opened over SSH can't be confused with a local one — the
            most frequent footgun reported in #683 was running a task
            on the wrong host because the header looked identical.
            """
            version, branch_name = _get_version_info()
            display_ver = _short_version(version)

            if branch_name:
                title = f"Terok {display_ver} [{branch_name}]"
            else:
                title = f"Terok {display_ver}"

            self.title = title
            self.sub_title = f"{getpass.getuser()}@{socket.gethostname()}"

        # ---------- Layout ----------

        def compose(self) -> ComposeResult:
            """Build the two-pane layout: projects/state on the left, tasks/details on the right."""
            # Use Textual's default Header which will show our title
            yield Header()

            # Main layout using grid
            with Horizontal(id="main"):
                # Left pane: project list (top) + selected project info (bottom)
                with Vertical(id="left-pane"):
                    project_list = ProjectList(id="project-list")
                    project_list.border_title = "Projects"
                    yield project_list
                    project_state = ProjectState(id="project-state")
                    project_state.border_title = "Project Details"
                    yield project_state
                    yield PanicButton(id="panic-button")
                # Right pane: tasks + task details
                with Vertical(id="right-pane"):
                    task_list = TaskList(id="task-list")
                    task_list.border_title = "Tasks"
                    yield task_list
                    task_details = TaskDetails(id="task-details")
                    task_details.border_title = "Task Details"
                    yield task_details

            yield StatusBar(id="status-bar")

            # Use Textual's default Footer which will show key bindings
            yield Footer()

        # -- Vim-style navigation -----------------------------------------
        # j/k move the cursor within whatever list or menu holds focus; h/l
        # hop between the two main panes.  Each is a no-op when the focus has
        # nowhere to go (a single-column menu, a button), so they stay quietly
        # inert outside the contexts where they make sense.

        def action_vim_down(self) -> None:
            """Move the focused list/menu cursor down (vim ``j``)."""
            self._vim_move_cursor("action_cursor_down")

        def action_vim_up(self) -> None:
            """Move the focused list/menu cursor up (vim ``k``)."""
            self._vim_move_cursor("action_cursor_up")

        def _vim_move_cursor(self, action: str) -> None:
            """Invoke *action* on the focused widget when it offers one."""
            move = getattr(self.focused, action, None)
            if callable(move):
                move()

        def action_vim_left(self) -> None:
            """Focus the left (projects) pane (vim ``h``)."""
            self._vim_focus_pane(ProjectList)

        def action_vim_right(self) -> None:
            """Focus the right (tasks) pane (vim ``l``)."""
            self._vim_focus_pane(TaskList)

        def _vim_focus_pane(self, pane: type) -> None:
            """Focus *pane*, but only while a main pane already holds focus.

            Pane-hopping is meaningful only on the main two-pane screen; inside
            a modal menu (where ``self.focused`` is some OptionList or Button)
            h/l must do nothing rather than yank focus out from under the modal.
            """
            if not isinstance(self.focused, (ProjectList, TaskList)):
                return
            try:
                self.query_one(pane).focus()
            except NoMatches:
                pass

        async def on_mount(self) -> None:
            """Load projects, restore selection state, and start polling on first mount."""
            # Apply the persisted theme choice before the first paint,
            # and keep ``tui.theme`` in sync with later palette picks.
            self._apply_saved_theme()

            # In a terok-managed tmux, mark this window as the one running
            # the TUI — self-healing across kill-and-relaunch (best-effort,
            # a few local socket calls; quiet no-op everywhere else).
            # Threaded so a hung tmux server can't stall startup for the
            # helper's subprocess timeouts.
            self.run_worker(
                tmux_session.stamp_main_window,
                name="stamp-tmux-window",
                group="tmux-stamp",
                thread=True,
                exit_on_error=False,
            )

            # Watch for a terok upgrade landing on disk under the running
            # TUI.  Restarting re-execs this process, which makes no sense
            # for a web-served TUI — there the server owns the lifecycle.
            if not self.is_web:
                self.set_interval(_UPDATE_CHECK_INTERVAL_S, self._check_for_update)

            try:
                clipboard_status = get_clipboard_helper_status()
                if not clipboard_status.available:
                    msg = "Clipboard copy unavailable: no clipboard helper found."
                    if clipboard_status.hint:
                        msg = f"{msg}\n{clipboard_status.hint}"
                    self.notify(msg, severity="warning", timeout=10)
            except Exception:
                # Clipboard helpers are best-effort; never block startup.
                pass

            # Load selection state before refreshing projects
            self._load_selection_state()

            await self.refresh_projects()
            # Re-drive any teardown a previous session left half-finished
            # before the operator can interact — see the method docstring.
            self._resume_interrupted_deletes()
            # Defer layout logging until after the first refresh cycle so
            # widgets have real sizes. This will help compare left vs right
            # panes and confirm whether the task list/details get space.
            try:
                self.call_after_refresh(self._log_layout_debug)
            except Exception:
                # call_after_refresh may not exist on very old Textual; in
                # that case we simply skip this extra logging.
                pass

            # If the DB exists but no resolver tier opens it, push the
            # unlock modal here — otherwise the main view falls back to
            # the ``maybe_vault_db`` graceful-degradation state and the
            # operator sees a silent empty SSH tile with no remediation.
            await self._refresh_vault_status(push_modal_if_locked=True)
            # Keep the pill live: external `vault unlock` / `lock` runs
            # surface without restarting the TUI.
            self._start_vault_watcher()

            # Once per session, surface the unconfirmed-recovery warning
            # as a notification — the pill catches the operator's eye
            # only on a TUI that's already running.  Anyone who's just
            # logged in (or just finished setup) deserves a louder
            # reminder; subsequent sessions get the pill alone.
            self._maybe_warn_recovery_unconfirmed()

            # First-run nudge: drive setup → wizard on a fresh install,
            # but also re-prompt for setup whenever a stale stamp is
            # detected (e.g. after a package upgrade).  The dismissed
            # flag persists through ``terok-state.json`` so a user who
            # closes both screens isn't nagged again — but a non-OK
            # verdict overrides the flag, since a stale stamp is
            # actionable feedback the user shouldn't be allowed to mute
            # indefinitely.
            await self._maybe_show_first_run_flow()

        def _apply_saved_theme(self) -> None:
            """Apply ``tui.theme`` from config, then track palette picks.

            An unknown name — a theme from a different Textual version,
            or a typo from hand-editing — falls back to the default
            silently: the config value is a preference, not a contract.
            The comparison seed is the theme *in effect after* the
            apply, so the signal echo of our own set (and of Textual's
            startup default when nothing is saved) never writes the
            default back into the user's config file.
            """
            saved = self._config.tui_theme
            if saved and saved in self.available_themes:
                self.theme = saved
            self._persisted_theme = self.theme
            self.theme_changed_signal.subscribe(self, self._on_theme_picked)

        def _on_theme_picked(self, theme: Theme) -> None:
            """Write a genuine palette theme pick through to the user config file."""
            if theme.name == self._persisted_theme:
                return
            try:
                save_tui_theme(theme.name)
            except (OSError, YAMLError) as exc:
                self.notify(
                    f"Theme applied for this session, but saving it failed: {exc}",
                    severity="warning",
                    timeout=10,
                )
                return
            self._persisted_theme = theme.name

        async def _maybe_show_first_run_flow(self) -> None:
            """Probe the setup verdict and decide whether to drive the first-run flow."""
            empty_install = not self._projects_by_id and not self._broken_by_id
            try:
                verdict = needs_setup()
            except Exception:
                verdict = SetupVerdict.OK  # keep the TUI usable on probe failure

            already_dismissed = getattr(self, "_first_run_dismissed", False)
            if verdict is SetupVerdict.OK and (already_dismissed or not empty_install):
                return

            self._first_run_dismissed = True
            self._save_selection_state()
            self._run_first_run_flow(verdict=verdict, empty_install=empty_install)

        @work(exclusive=True, group="first-run-flow", exit_on_error=False)
        async def _run_first_run_flow(self, *, verdict: SetupVerdict, empty_install: bool) -> None:
            """Drive setup → wizard sequencing on a single worker.

            Runs the setup flow first when the verdict is non-OK, then
            chains into the new-project wizard *only* if setup
            succeeded (or wasn't needed) and the install is empty.
            Surfaces unexpected exceptions as a TUI notification so a
            crash in either screen can never silently swallow the
            user's first impression.
            """
            try:
                proceed = await self._run_setup_flow(verdict)
                if proceed and empty_install:
                    self.action_new_project_wizard()
            except Exception as exc:  # noqa: BLE001 — last-resort surface
                self.notify(
                    f"First-run flow failed: {exc}",
                    severity="error",
                    timeout=15,
                )
                raise

        async def _run_setup_flow(self, verdict: SetupVerdict, *, force: bool = False) -> bool:
            """Show the setup screen + worker log; return True if the wizard may follow.

            ``True`` covers both "setup completed cleanly" and "verdict
            was already OK so we skipped the screen" — either way the
            host stack is in a state where the project wizard makes
            sense.  ``False`` covers user-initiated skip / refusal /
            failure: the wizard is gated behind a working sandbox stack,
            so chaining into it on a known-broken host would just
            produce confusing follow-on errors.

            *force* bypasses the OK short-circuit: the auto-first-run
            flow uses ``force=False`` (don't nag a healthy install);
            palette invocations use ``force=True`` (the user explicitly
            asked for the dialog, so honour it — re-running setup is
            idempotent and the screen's headline already says so).
            """
            if verdict is SetupVerdict.OK and not force:
                return True

            outcome = await self.push_screen_wait(SetupScreen(verdict=verdict))
            match outcome:
                case SetupOutcome.SHOULD_RUN:
                    return await self._run_setup_subprocess()
                case SetupOutcome.SKIPPED | SetupOutcome.CANCELLED:
                    self.notify(
                        "Skipped terok setup.  Run it any time from the command "
                        "palette → Run terok setup.",
                        severity="warning",
                        timeout=10,
                    )
                case SetupOutcome.REFUSED:
                    self.notify(
                        "Setup refused due to a downgrade.  Re-upgrade or remove "
                        "the setup stamp; see ``terok setup`` from a shell for "
                        "details.",
                        severity="error",
                        timeout=15,
                    )
            return False

        async def _run_setup_subprocess(self) -> bool:
            """Stream ``terok setup`` through a ``WorkerLogScreen``; True on exit 0.

            The view is pushed non-blocking and the *entry* — not the
            screen — is awaited: hiding the log to the background must
            not stop the first-run flow from chaining into the wizard
            once setup actually finishes.

            Exit code 5 — every install phase succeeded but the SELinux
            policy is still missing on a socket-mode host — routes through
            [`_offer_selinux_fix`][terok.tui.app.TerokTUI._offer_selinux_fix]
            so the user can pick Install / Switch-to-TCP from a modal
            instead of having to drop to a shell.

            The credentials pre-flight runs first: the subprocess is
            captured and TTY-less, so the passphrase-tier conversation
            must happen up front in TUI modals — see
            [`_ensure_credentials_provisioned`][terok.tui.app.TerokTUI._ensure_credentials_provisioned].
            """
            if not await self._ensure_credentials_provisioned():
                return False
            entry = self.dispatch_console_command(["terok", "setup"], title="Running terok setup")
            await self.push_screen(WorkerLogScreen(entry))
            await entry.wait()
            if entry.ok:
                return True
            if entry.exit_code == _EXIT_MANUAL_STEP_NEEDED:
                return await self._offer_selinux_fix()
            self.notify(
                "terok setup reported errors.  Re-run from the command palette "
                "once the underlying issue is fixed.",
                severity="error",
                timeout=15,
            )
            return False

        async def _ensure_credentials_provisioned(self) -> bool:
            """Collect the passphrase-tier decision before dispatching ``terok setup``.

            The setup subprocess runs captured and TTY-less, so sandbox's
            credentials phase can neither show its chooser nor announce a
            fresh mint to ``/dev/tty`` — it would fail closed after other
            phases already ran, scaring the user with an error they were
            never given a chance to prevent.  The TUI has the conversation
            up front instead: tier chooser → create-passphrase modal →
            in-process provisioning through the sandbox library → reveal +
            recovery ack for a minted value.  The subprocess then finds
            the tier already resolving and passes.

            With systemd-creds available the strongest tier picks itself
            (mirroring the CLI's auto-detect) and only the mint + reveal
            remain.  Provisioning in-process — never ``--echo-passphrase``
            into the captured log — keeps the recovery key out of every
            log surface, which is the whole point of sandbox's
            controlling-TTY announce design.

            Returns ``True`` when setup may proceed (already provisioned,
            or provisioning just landed), ``False`` on cancel or failure
            (both already notified).
            """
            import asyncio

            from terok.lib.api.vault import plan_provisioning, provision_passphrase_tier

            from .screens import VaultCreatePassphraseModal, VaultTierChooserModal

            cfg = SandboxConfig()
            # The plan probe gets the same user-facing error path as the
            # provisioning call below: it fails closed
            # (WrongPassphraseError) on a configured-but-broken durable
            # tier, and an unnotified crash here would strand the
            # palette worker in a silent ERROR state.
            try:
                plan = await asyncio.to_thread(plan_provisioning, cfg)
            except Exception as exc:  # noqa: BLE001 — e.g. an unsealable systemd-creds credential
                self.notify(
                    f"Vault passphrase probe failed: {exc}",
                    severity="error",
                    timeout=15,
                )
                return False
            if plan.provisioned:
                return True

            typed: str | None = ""  # the create-modal's "generate for me" sentinel
            tier: str | None
            if plan.auto_tier is not None:
                tier = plan.auto_tier
            else:
                tier = await self.push_screen_wait(
                    VaultTierChooserModal(keyring_available=plan.keyring_available)
                )
                if tier is None:
                    self._notify_provisioning_skipped()
                    return False
                typed = await self.push_screen_wait(VaultCreatePassphraseModal())
                if typed is None:
                    self._notify_provisioning_skipped()
                    return False

            try:
                result = await asyncio.to_thread(
                    provision_passphrase_tier, cfg, tier=tier, passphrase=typed or None
                )
            except Exception as exc:  # noqa: BLE001 — surface keyring/seal failures verbatim
                self.notify(
                    f"Vault passphrase provisioning failed: {exc}",
                    severity="error",
                    timeout=15,
                )
                return False

            await self._refresh_vault_status()
            if result.generated:
                await self._reveal_new_passphrase(result.passphrase, result.source)
            return True

        def _notify_provisioning_skipped(self) -> None:
            """One warning for every cancel exit of the provisioning conversation."""
            self.notify(
                "Setup skipped — vault encryption needs a passphrase tier "
                "first.  Re-run from the command palette → Run terok setup.",
                severity="warning",
                timeout=10,
            )

        async def _reveal_new_passphrase(self, passphrase: str, source: str) -> None:
            """Show a freshly minted passphrase once and record the operator's save ack.

            The counterpart of the CLI's bold-yellow ``/dev/tty`` announce:
            the value appears in exactly one modal, never in the console
            log.  "Mark as saved" writes the recovery marker; closing
            without it leaves the pill's UNSAVED warning as the nudge.
            """
            from .screens import VaultRevealModal

            outcome = await self.push_screen_wait(
                VaultRevealModal(passphrase, source, already_acked=False)
            )
            if outcome is True:
                RecoveryStatus.acknowledge(SandboxConfig())
                await self._refresh_vault_status()

        @work(exclusive=True, group="vault-provision", exit_on_error=False)
        async def _run_vault_provision_flow(self) -> None:
            """First-passphrase flow for the palette's unlock action on a fresh install.

            Reached when the operator picks "unlock" but no credentials DB
            exists — there is nothing to unlock, and the old modal would
            have silently keyed the future vault to whatever they typed,
            on the reboot-volatile session tier.  Runs the same chooser +
            create + reveal conversation as the setup pre-flight.  A
            worker method because the conversation needs
            ``push_screen_wait``.
            """
            if await self._ensure_credentials_provisioned():
                self.notify(
                    "Vault passphrase provisioned — the encrypted DB is created on first use.",
                    severity="information",
                    timeout=8,
                )

        @work(exclusive=True, group="vault-change", exit_on_error=False)
        async def _run_vault_change_flow(self) -> None:
            """Change the vault passphrase — the TUI rendering of the CLI verb.

            Drives sandbox's ``change_passphrase`` (the same prompt-free
            core behind ``vault passphrase change``): verify → rekey the
            DB → rewrite every tier holding the old value → drop the
            recovery marker.  The *current* passphrase is asked for only
            when the vault is locked — when a tier resolves it, retyping
            would be theatre (Reveal prints it to the same operator).
            The new value comes from the create modal (typed-and-
            confirmed, or Enter to generate) and is revealed + re-acked
            after the change succeeded — the previously confirmed copy
            is now the wrong passphrase.
            """
            import asyncio

            from terok.lib.api.vault import (
                VaultState,
                WrongPassphraseError,
                change_passphrase,
                load_vault_status,
            )

            from .screens import VaultCreatePassphraseModal, VaultUnlockModal

            try:
                status = await asyncio.to_thread(load_vault_status)
            except Exception as exc:  # noqa: BLE001 — surface probe failures verbatim
                self.notify(f"Vault probe failed: {exc}", severity="error", timeout=15)
                return
            if status.state is VaultState.UNPROVISIONED:
                self.notify(
                    "Nothing to change — the vault has no passphrase yet."
                    "  Run terok setup (or the unlock action) to provision one first.",
                    severity="warning",
                    timeout=10,
                )
                return

            old: str | None = None
            if status.state is not VaultState.UNLOCKED:
                old = await self.push_screen_wait(
                    VaultUnlockModal(
                        title="Vault locked — current passphrase needed",
                        prompt=(
                            "Enter the CURRENT credentials-DB passphrase.\n"
                            "The change re-encrypts the DB, so the old key must open it first."
                        ),
                        confirm_label="Continue",
                    )
                )
                if not old:
                    self.notify("Passphrase change cancelled.", severity="warning", timeout=8)
                    return

            typed = await self.push_screen_wait(VaultCreatePassphraseModal())
            if typed is None:
                self.notify("Passphrase change cancelled.", severity="warning", timeout=8)
                return

            cfg = SandboxConfig()
            try:
                result = await asyncio.to_thread(change_passphrase, cfg, old=old, new=typed or None)
            except WrongPassphraseError:
                self.notify(
                    "That passphrase does not open the credentials DB — nothing was changed.",
                    severity="error",
                    timeout=10,
                )
                return
            except Exception as exc:  # noqa: BLE001 — every failure becomes a notification
                if "database is locked" in str(exc).lower():
                    self.notify(
                        "Cannot re-encrypt the credentials DB while it is in use —"
                        " a running task's supervisor still holds it open.  Stop or"
                        " delete the running tasks, then retry.  Nothing was changed.",
                        severity="error",
                        timeout=20,
                    )
                elif isinstance(exc, (ValueError, RuntimeError)):
                    # change_passphrase's own refusals (identical/empty value,
                    # passphrase-command tier) arrive fully worded.
                    self.notify(f"Nothing was changed: {exc}", severity="error", timeout=15)
                else:
                    self.notify(f"Passphrase change failed: {exc}", severity="error", timeout=20)
                return

            if result.problems:
                details = "\n".join(f"{p.tier}: {p.detail}" for p in result.problems)
                self.notify(
                    "The vault now uses the new passphrase, but some tiers could"
                    f" not be rewritten:\n{details}",
                    title="Passphrase changed — tiers need attention",
                    severity="error",
                    timeout=30,
                )
            else:
                self.notify(
                    "Vault passphrase changed — every stored tier now holds the"
                    " new value.  Already-running tasks keep the old one until"
                    " restarted.",
                    severity="information",
                    timeout=10,
                )
            await self._refresh_vault_status()
            # The recovery marker was dropped with the old passphrase —
            # reveal the new value (minted or typed) and collect a fresh
            # save-acknowledgement.
            source = (
                str(self._last_vault_status.source)
                if (self._last_vault_status and self._last_vault_status.source)
                else "vault"
            )
            await self._reveal_new_passphrase(result.passphrase, source)

        async def _offer_selinux_fix(self) -> bool:
            """Push the SELinux-fix modal; on a chosen remediation, re-run setup.

            Both remediations (install policy / switch to TCP mode)
            dispatch through the standard console-log pipeline so the
            operator sees the streaming output, then a fresh setup run
            picks up the now-resolvable state.  The recursion is
            bounded — a second exit-5 just re-opens the same modal.
            """
            from .selinux_fix_screen import SelinuxFixOutcome, SelinuxFixScreen

            outcome = await self.push_screen_wait(SelinuxFixScreen())
            if outcome is SelinuxFixOutcome.SKIPPED:
                self.notify(
                    "SELinux policy is still missing.  Run setup again "
                    "from the command palette once it's installed, or "
                    "switch services.mode to tcp manually.",
                    severity="warning",
                    timeout=12,
                )
                return False
            if outcome is SelinuxFixOutcome.INSTALL_POLICY:
                entry = self.dispatch_console_command(
                    [
                        "python",
                        "-c",
                        "from terok.tui.worker_actions import selinux_install_policy; "
                        "selinux_install_policy()",
                    ],
                    title="Installing SELinux policy (sudo bash …)",
                )
            else:  # SWITCH_TO_TCP
                entry = self.dispatch_console_command(
                    [
                        "python",
                        "-c",
                        "from terok.tui.worker_actions import selinux_switch_to_tcp; "
                        "selinux_switch_to_tcp()",
                    ],
                    title="Switching services.mode to tcp",
                )
            await self.push_screen(WorkerLogScreen(entry))
            await entry.wait()
            if not entry.ok:
                self.notify(
                    "The remediation step failed — see the log view for details.",
                    severity="error",
                    timeout=15,
                )
                return False
            return await self._run_setup_subprocess()

        @work(exclusive=True, group="first-run-flow", exit_on_error=False)
        async def action_run_setup(self) -> None:
            """Open the setup flow on demand (command palette / re-run).

            Always probes the current verdict so the user sees the live
            state — useful even on a healthy install when they want to
            re-apply the (idempotent) systemd cycle after an upgrade.
            Passes ``force=True`` so an OK verdict shows the dialog
            (with its "re-running is safe but optional" headline)
            instead of silently no-op'ing on healthy installs.

            Runs on a worker because ``_run_setup_flow`` calls
            ``push_screen_wait``, which requires a worker context.
            """
            try:
                verdict = needs_setup()
            except Exception:
                verdict = SetupVerdict.FIRST_RUN
            await self._run_setup_flow(verdict, force=True)

        def _log_layout_debug(self) -> None:
            """Write a one-shot snapshot of key widget sizes to the state dir.

            This is for debugging why the right-hand task list/details may
            not be visible even though the widgets exist.
            """
            try:
                log_path = self._config.core_state_dir / "terok.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)

                left_pane = self.query_one("#left-pane")
                right_pane = self.query_one("#right-pane")
                project_list = self.query_one("#project-list", ProjectList)
                project_state = self.query_one("#project-state", ProjectState)
                task_list = self.query_one("#task-list", TaskList)
                task_details = self.query_one("#task-details", TaskDetails)

                with log_path.open("a", encoding="utf-8") as _f:
                    _f.write("[terok DEBUG] layout snapshot after refresh:\n")
                    _f.write(f"  left-pane   size={left_pane.size} region={left_pane.region}\n")
                    _f.write(f"  right-pane  size={right_pane.size} region={right_pane.region}\n")
                    _f.write(
                        f"  proj-list   size={project_list.size} region={project_list.region}\n"
                    )
                    _f.write(
                        f"  proj-state  size={project_state.size} region={project_state.region}\n"
                    )
                    _f.write(f"  task-list   size={task_list.size} region={task_list.region}\n")
                    _f.write(
                        f"  task-det    size={task_details.size} region={task_details.region}\n"
                    )
            except Exception:
                pass

        def _log_debug(self, message: str) -> None:
            """Append a simple debug line to the TUI log file.

            This is intentionally very small and best-effort so it never
            interferes with normal TUI behavior. It shares the same log
            path as `_log_layout_debug` for easier inspection.
            """

            try:
                from datetime import datetime as _dt

                log_path = self._config.core_state_dir / "terok.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                ts = _dt.now().isoformat(timespec="seconds")
                with log_path.open("a", encoding="utf-8") as _f:
                    _f.write(f"[terok DEBUG] {ts} {message}\n")
            except Exception:
                # Logging must never break the TUI.
                pass

        def _load_selection_state(self) -> None:
            """Load last selected project and tasks from persistent storage."""
            try:
                import json

                state_path = self._config.core_state_dir / "terok-state.json"
                if state_path.exists():
                    with state_path.open("r", encoding="utf-8") as f:
                        state = json.load(f)
                        self._last_selected_project = state.get("last_project")
                        self._last_selected_tasks = state.get("last_tasks", {})
                        self._first_run_dismissed = bool(state.get("first_run_dismissed", False))
            except Exception:
                # If loading fails, just start with empty state
                self._last_selected_project = None
                self._last_selected_tasks = {}
                self._first_run_dismissed = False

        def _save_selection_state(self) -> None:
            """Save current selection state to persistent storage."""
            try:
                import json

                state_path = self._config.core_state_dir / "terok-state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state = {
                    "last_project": self.current_project_name,
                    "last_tasks": self._last_selected_tasks,
                    "first_run_dismissed": getattr(self, "_first_run_dismissed", False),
                }
                with state_path.open("w", encoding="utf-8") as f:
                    json.dump(state, f)
            except Exception:
                # If saving fails, just ignore - it's not critical
                pass

        # ---------- Helpers ----------

        async def refresh_projects(self) -> None:
            """Reload projects from disk and rebuild every dependent pane."""
            projects, broken = discover_projects()
            self._projects_by_id = {p.name: p for p in projects}
            self._broken_by_id = {bp.name: bp for bp in broken}

            proj_widget = self.query_one("#project-list", ProjectList)
            proj_widget.set_projects(projects, broken)
            self._announce_newly_broken(broken)

            if not projects and not broken:
                self._render_empty_project_state()
                return

            self._restore_or_default_project_selection(proj_widget, projects, broken)
            self._last_project_state = None
            self._last_image_old = None

            if self.current_project_name in self._broken_by_id:
                # Broken projects have no loadable config; ``refresh_tasks`` and
                # the polling loop would raise inside ``load_project``.
                self._render_broken_selection(self.current_project_name)
            else:
                await self.refresh_tasks()
                self._start_upstream_polling()

        def _announce_newly_broken(self, broken: list[BrokenProject]) -> None:
            """Toast once when the set of broken-project names changes.

            Users upgrading across a schema change need to see the breakage
            immediately — but only once per distinct set, so repeated
            ``refresh_projects`` calls don't spam (#565).
            """
            current_ids = {bp.name for bp in broken}
            if not current_ids:
                # Breakages cleared — forget announced so a regression re-fires.
                self._announced_broken_ids = set()
                return
            if current_ids == self._announced_broken_ids:
                return
            first = broken[0]
            summary = f"{first.name}: {first.error.splitlines()[0]}"
            extra = f" (+{len(broken) - 1} more)" if len(broken) > 1 else ""
            self.notify(
                f"Broken project config detected — {summary}{extra}",
                severity="warning",
                timeout=15,
            )
            self._announced_broken_ids = current_ids

        def _restore_or_default_project_selection(
            self,
            proj_widget: ProjectList,
            projects: list[ProjectConfig],
            broken: list[BrokenProject],
        ) -> None:
            """Restore the previously-selected project, or fall back to the first row."""
            candidate_ids = [bp.name for bp in broken] + [p.name for p in projects]
            last_project = self._last_selected_project
            if last_project and last_project in candidate_ids:
                self.current_project_name = last_project
                proj_widget.select_project(self.current_project_name)
            elif self.current_project_name is None:
                self.current_project_name = candidate_ids[0]
                proj_widget.select_project(self.current_project_name)

        def _render_empty_project_state(self) -> None:
            """Clear every pane when no projects exist on disk at all."""
            self.current_project_name = None
            self._last_project_state = None
            self._last_image_old = None
            self.query_one("#task-list", TaskList).set_tasks("", [])
            self.query_one("#task-details", TaskDetails).set_task(None)
            self.query_one("#project-state", ProjectState).set_state(None, None, None)

        def _render_broken_selection(self, project_name: str) -> None:
            """Render a broken project's error and clear the task/details panes."""
            bp = self._broken_by_id.get(project_name)
            state_widget = self.query_one("#project-state", ProjectState)
            if bp is None:
                state_widget.set_state(None, None, None)
                return
            state_widget.set_broken(bp)
            task_list = self.query_one("#task-list", TaskList)
            task_list.set_tasks(project_name, [])
            task_details = self.query_one("#task-details", TaskDetails)
            task_details.set_task(None)
            self.current_task = None

        async def refresh_tasks(self) -> None:
            """Reload tasks for the current project and update the task list."""
            if not self.current_project_name:
                return
            pid = self.current_project_name
            tasks_meta = get_tasks(pid, reverse=True)
            self._seed_task_rows(pid, tasks_meta)
            task_list = self.query_one("#task-list", TaskList)
            task_list.set_tasks(pid, tasks_meta)

            if task_list.tasks:
                # Try to restore last selected task for this project
                last_task_id = self._last_selected_tasks.get(self.current_project_name)
                desired_idx = 0
                if last_task_id:
                    for idx, task in enumerate(task_list.tasks):
                        if task.task_id == last_task_id:
                            desired_idx = idx
                            break

                self.current_task = task_list.tasks[desired_idx]

                # Defer index setting to after layout pass so appended items
                # are fully mounted.  An immediate ``index = 0`` after clear()
                # is a no-op because clear() already reset the index to 0.
                def _apply_selection(idx: int = desired_idx) -> None:
                    """Set the task list index after layout is complete."""
                    try:
                        task_list.index = idx
                        task_list._post_selected_task()
                    except Exception:
                        pass

                self.call_after_refresh(_apply_selection)
            else:
                self.current_task = None

            self._update_task_details()

            task_count = len(task_list.tasks)
            self._last_task_count = task_count
            # Update project state panel (Dockerfiles/images/SSH/cache + task count)
            self._refresh_project_state(task_count=task_count)

        def _seed_task_rows(self, pid: str, tasks_meta: "list[TaskMeta]") -> None:
            """Seed freshly loaded rows with TUI-held state, in place.

            ``get_tasks`` reads only the on-disk metadata; the ⏳ launching
            flag and the container states live with the TUI.  Seeding both
            before ``set_tasks`` makes the first label render already correct
            — no reformatting pass, and no ❓ "not found" flicker while the
            batch state poll is still in flight or going unanswered during a
            concurrent image build (#1134).
            """
            known_states = self._last_container_states.get(pid, {})
            for tm in tasks_meta:
                tm.starting = (pid, tm.task_id) in self._launching_tasks
                tm.container_state = known_states.get(tm.task_id)

        def _resume_interrupted_deletes(self) -> None:
            """Re-queue deletes a previous session started but never finished.

            A task is flagged ``deleting`` on disk the instant a delete begins,
            *before* the background teardown runs.  If the TUI dies in between,
            the flag survives the crash (and reboots) yet no worker is left to
            act on it — the task is stranded ``deleting`` forever, and the
            delete guard refuses to retry it.  On startup we sweep every loaded
            project for such orphans and re-queue their teardown, which is
            idempotent and best-effort, so the interrupted delete simply runs
            to completion.
            """
            resumed = 0
            for pid in self._projects_by_id:
                for task in get_tasks(pid):
                    if not task.deleting or (pid, task.task_id) in self._deleting_tasks:
                        continue
                    self._queue_task_delete(pid, task.task_id, task.name or "")
                    resumed += 1
            if resumed:
                self.notify(f"Resuming {resumed} interrupted task deletion(s)...")

        def _update_task_details(self) -> None:
            """Refresh the task details panel for the currently selected task."""
            details = self.query_one("#task-details", TaskDetails)
            if self.current_task is None:
                details.set_task(None)
                return
            details.set_task(self.current_task)
            if not self.current_task.deleting:
                self._queue_task_image_status(self.current_project_name, self.current_task)

        # ---------- Launch tracking ----------

        def _apply_launching_to_tasks(self) -> None:
            """Mirror ``_launching_tasks`` onto current ``TaskMeta`` instances and repaint."""
            pid = self.current_project_name
            if pid is None:
                return
            task_list = self.query_one("#task-list", TaskList)
            for tm in task_list.tasks:
                tm.starting = (pid, tm.task_id) in self._launching_tasks
            task_list.refresh_labels()
            if self.current_task is not None:
                self.current_task.starting = (
                    pid,
                    self.current_task.task_id,
                ) in self._launching_tasks
                self.query_one("#task-details", TaskDetails).set_task(self.current_task)

        def _mark_launching(self, project_name: str, task_id: str) -> None:
            """Flag a task as currently being launched.  Triggers a repaint."""
            key = (project_name, task_id)
            if key in self._launching_tasks:
                return
            self._launching_tasks.add(key)
            if project_name == self.current_project_name:
                self._apply_launching_to_tasks()

        def _unmark_launching(self, project_name: str, task_id: str) -> None:
            """Clear the launching flag once the worker has reached a terminal state."""
            key = (project_name, task_id)
            if key not in self._launching_tasks:
                return
            self._launching_tasks.discard(key)
            if project_name == self.current_project_name:
                self._apply_launching_to_tasks()

        # ---------- Status / notifications ----------

        def _refresh_project_state(self, task_count: int | None = None) -> None:
            """Update the small project state summary panel.

            This is called whenever the current project changes or when actions
            that affect infrastructure state (generate/build/ssh/cache) finish.
            """
            state_widget = self.query_one("#project-state", ProjectState)

            if not self.current_project_name:
                state_widget.set_state(None, None, None)
                return
            if task_count is not None:
                self._last_task_count = task_count

            project_name = self.current_project_name
            project = self._projects_by_id.get(project_name)
            if project is not None:
                state_widget.show_loading(project, self._last_task_count)
            else:
                state_widget.update("Loading project details...")

            self.run_worker(
                lambda: self._load_project_state(project_name),
                name=f"project-state:{project_name}",
                group="project-state",
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        def _load_project_state(self, project_name: str) -> ProjectStateResult:
            """Load project infrastructure state in a background thread."""
            try:
                project = load_project(project_name)
                gate = make_git_gate(project)

                def _gate_commit_provider(_pid: str) -> dict | None:
                    info = gate.last_commit()
                    return dict(info) if info is not None else None

                state = Project(project).state(gate_commit_provider=_gate_commit_provider)
                staleness = None
                if state.get("gate") and project.upstream_url:
                    try:
                        staleness = gate.compare_vs_upstream()
                    except Exception:
                        staleness = None
                try:
                    shield_env = _shield_check_environment()
                except Exception:
                    shield_env = None
                return ProjectStateResult(
                    project_name,
                    project,
                    state,
                    staleness,
                    shield_env=shield_env,
                )
            except SystemExit as e:
                return ProjectStateResult(project_name, error=str(e))
            except Exception as e:
                return ProjectStateResult(project_name, error=str(e))

        def _queue_task_image_status(self, project_name: str | None, task: TaskMeta | None) -> None:
            """Schedule a background check for whether the task's image is outdated."""
            if not project_name or task is None:
                return
            if task.deleting:
                return

            task_id = task.task_id
            self.run_worker(
                lambda: self._load_task_image_status(project_name, task),
                name=f"task-image:{project_name}:{task_id}",
                group="task-image",
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        def _load_task_image_status(
            self, project_name: str, task: TaskMeta
        ) -> tuple[str, str, bool | None]:
            """Check whether a task's container image is outdated (runs in thread)."""
            image_old = Task(load_project(project_name), task).image_is_old()
            return project_name, task.task_id, image_old

        def _query_shield_state(self, project_name: str, task: TaskMeta) -> None:
            """Schedule a background worker to query shield state for a task."""
            if not task.mode:
                return
            tid = task.task_id
            self.run_worker(
                lambda: self._load_shield_state(project_name, task),
                name=f"shield-state:{project_name}:{tid}",
                group="shield-state",
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        @staticmethod
        def _load_shield_state(project_name: str, task: TaskMeta) -> tuple[str, str, str | None]:
            """Query shield state for a task (runs in thread)."""
            try:
                project = load_project(project_name)
                mode = task.mode or "cli"
                cname = container_name(project_name, mode, task.task_id)
                task_dir = project.tasks_root / str(task.task_id)
                st = ShieldManager(task_dir).state(cname)
                # ``ShieldManager.state`` returns an Any-annotated
                # ShieldState (importlinter keeps terok_shield out of
                # this layer); the runtime value is an enum whose
                # ``.name`` is the display string.
                return project_name, task.task_id, st.name
            except Exception:
                return project_name, task.task_id, None

        # ---------- Selection handlers (from widgets) ----------

        @on(ProjectList.ProjectSelected)
        async def handle_project_selected(self, message: ProjectList.ProjectSelected) -> None:
            """Called when user selects a project in the list."""
            self.current_project_name = message.project_name
            self._last_project_state = None
            # Save the project selection
            self._last_selected_project = self.current_project_name
            self._save_selection_state()

            # Broken projects have no loadable config, so the usual task /
            # polling / state-worker pipeline would just raise.  Render the
            # validation error instead and leave the rest of the panes idle
            # until a healthy project is selected again (#565).
            if message.is_broken:
                self._stop_upstream_polling()
                self._stop_container_status_polling()
                self._render_broken_selection(message.project_name)
                return

            await self.refresh_tasks()
            # Start polling for the newly selected project
            self._start_upstream_polling()
            self._start_container_status_polling()

        @on(TaskList.TaskSelected)
        async def handle_task_selected(self, message: TaskList.TaskSelected) -> None:
            """Called when user selects a task in the list."""
            self.current_project_name = message.project_name
            self.current_task = message.task
            self._last_image_old = None

            # Save the task selection for this project
            if self.current_project_name and self.current_task:
                self._last_selected_tasks[self.current_project_name] = self.current_task.task_id
                self._save_selection_state()

            self._update_task_details()

            # Immediately check container state when task is selected
            if self.current_task and self.current_task.mode:
                self._queue_container_state_check(message.project_name)
                self._query_shield_state(message.project_name, self.current_task)

        @on(Worker.StateChanged)
        async def handle_worker_state_changed(self, event: Worker.StateChanged) -> None:
            """Dispatch completed worker results to the appropriate UI panel."""
            worker = event.worker
            if event.state != WorkerState.SUCCESS:
                if worker.group == "project-state" and event.state == WorkerState.ERROR:
                    state_widget = self.query_one("#project-state", ProjectState)
                    state_widget.update(f"Project state error: {worker.error}")
                return

            if worker.group == "project-state":
                result = worker.result
                if not result:
                    return
                psr: ProjectStateResult = result
                if psr.project_name != self.current_project_name:
                    return
                state_widget = self.query_one("#project-state", ProjectState)
                if psr.error:
                    state_widget.update(f"Project state error: {psr.error}")
                    return
                if psr.project is None or psr.state is None:
                    state_widget.set_state(None, None, None)
                    return
                self._projects_by_id[psr.project_name] = psr.project
                self._staleness_info = psr.staleness
                self._last_project_state = psr.state
                self._last_shield_env = psr.shield_env
                state_widget.set_state(
                    psr.project,
                    psr.state,
                    self._last_task_count,
                    self._staleness_info,
                    shield_env=psr.shield_env,
                )
                return

            if worker.group == "task-image":
                result = worker.result
                if not result:
                    return
                project_name, task_id, image_old = result
                if project_name != self.current_project_name:
                    return
                if not self.current_task or self.current_task.task_id != task_id:
                    return
                self._last_image_old = image_old
                details = self.query_one("#task-details", TaskDetails)
                details.set_task(self.current_task, image_old=image_old)
                return

            if worker.group == "container-state":
                result = worker.result
                if not result:
                    return
                project_name, metas = result
                if project_name != self.current_project_name:
                    return
                if metas is None:
                    # The runtime didn't answer (podman busy — e.g. locked by
                    # a concurrent image build).  Keep the last-known rows:
                    # only a successful query may move a status (#1134).
                    return
                self._last_container_states[project_name] = {
                    m.task_id: m.container_state for m in metas
                }
                task_list = self.query_one("#task-list", TaskList)
                # The batch query re-reads the on-disk task set every tick, so
                # its task IDs reveal tasks created or deleted outside the TUI.
                # Reconcile membership first: this level-triggered diff against
                # the source of truth can't miss a change the way edge-polling
                # could, and it converges in a single tick.
                fresh_by_id = {m.task_id: m for m in metas}
                if set(fresh_by_id) != {tm.task_id for tm in task_list.tasks}:
                    await self.refresh_tasks()
                    # Membership moved: re-point the inotify watch so the new
                    # tasks' agent-config dirs are watched (and gone ones freed).
                    self._resync_task_watches()
                    return
                # Membership matches: refresh the live lifecycle fields on each
                # displayed row in place.  The poll re-reads more than the
                # container state — the ``ready_at`` init marker, work status
                # and exit code drift while a row stays put — so a row whose
                # container is already "running" still needs its ``initialized``
                # flag synced to flip from "init" to "running".  Repaint only
                # when a row's rendered status badge actually moves.
                changed = False
                for tm in task_list.tasks:
                    before = (tm.status, tm.work_status, tm.web_port)
                    tm.adopt_live_state(fresh_by_id[tm.task_id])
                    if (tm.status, tm.work_status, tm.web_port) != before:
                        changed = True
                if changed:
                    # Regenerate labels on visible list items so status badges update
                    task_list.refresh_labels()
                    if self.current_task:
                        details = self.query_one("#task-details", TaskDetails)
                        details.set_task(self.current_task)
                return

            if worker.group == "task-delete":
                result = worker.result
                if not result:
                    return
                project_name, task_id, task_name, error, warnings = result
                self._deleting_tasks.discard((project_name, task_id))
                task_label = f"{project_name} {task_id}" + (f" {task_name}" if task_name else "")
                if error:
                    self.notify(f"Delete error for task {task_label}: {error}")
                elif warnings:
                    detail = "; ".join(warnings)
                    self.notify(
                        f"Deleted task {task_label} with warnings:\n{detail}\n"
                        f"Archive: terok task archive list {project_name}",
                    )
                else:
                    self.notify(
                        f"Deleted task {task_label}.\n"
                        f"Archive: terok task archive list {project_name}",
                    )

                if project_name != self.current_project_name:
                    return
                await self.refresh_tasks()

            if worker.group == "unattended-launch":
                result = worker.result
                if not result:
                    return
                project_name, task_id, error = result
                if error:
                    self.notify(f"Unattended error: {error}")
                elif task_id:
                    self._focus_task_after_creation(project_name, task_id)
                    self.notify(f"Unattended task {task_id} started for {project_name}")
                    self._start_unattended_watcher(project_name, task_id)
                if project_name == self.current_project_name:
                    await self.refresh_tasks()
                return

            if worker.group == "unattended-wait":
                result = worker.result
                if not result:
                    return
                project_name, task_id, exit_code, error = result
                if error:
                    self.notify(f"Unattended watcher error for task {task_id}: {error}")
                elif exit_code == 0:
                    self.notify(f"Unattended task {task_id} completed successfully")
                else:
                    self.notify(f"Unattended task {task_id} failed (exit {exit_code})")
                if project_name == self.current_project_name:
                    await self.refresh_tasks()
                return

            if worker.group == "followup-launch":
                result = worker.result
                if not result:
                    return
                project_name, task_id, error = result
                if error:
                    self.notify(f"Follow-up error: {error}")
                else:
                    self.notify(f"Follow-up started for task {task_id}")
                    self._start_unattended_watcher(project_name, task_id)
                if project_name == self.current_project_name:
                    await self.refresh_tasks()
                return

            if worker.group == "shield-action":
                result = worker.result
                if not result:
                    return
                project_name, task_id, error = result
                if error:
                    self.notify(f"Shield action failed: {error}")
                else:
                    # Extract action from worker name ("shield-action:down:pid:tid")
                    parts = (worker.name or "").split(":")
                    action = parts[1] if len(parts) >= 2 else ""
                    if action == "down":
                        self.notify(
                            f"Shield dropped for task {task_id}. {self._config.shield_security_hint}"
                        )
                    elif action == "up":
                        self.notify(f"Shield up for task {task_id}")
                # Refresh shield state after action
                if (
                    self.current_task
                    and self.current_task.task_id == task_id
                    and project_name == self.current_project_name
                ):
                    self._query_shield_state(project_name, self.current_task)
                return

            if worker.group == "shield-state":
                result = worker.result
                if not result:
                    return
                project_name, task_id, shield_st = result
                if project_name != self.current_project_name:
                    return
                if not self.current_task or self.current_task.task_id != task_id:
                    return
                self.current_task.shield_state = shield_st
                details = self.query_one("#task-details", TaskDetails)
                details.set_task(self.current_task, image_old=self._last_image_old)
                return

            if worker.group == "panic":
                panic_result = worker.result
                if not panic_result:
                    return
                self._last_panic_result = panic_result
                report = format_panic_report(panic_result)
                severity: Literal["error", "warning"] = (
                    "error" if panic_result.has_errors else "warning"
                )
                self.notify(report, severity=severity, timeout=30)
                await self.refresh_tasks()
                self._refresh_project_state()
                if panic_result.total_running > 0:
                    await self.push_screen(
                        ConfirmDestructiveScreen(
                            "Resource access has been cut.\n\nAlso kill all containers?",
                            title="Kill Containers?",
                            confirm_label="Kill",
                        ),
                        self._on_panic_stop_confirmed,
                    )
                return

            if worker.group == "panic-stop":
                stop_result = worker.result
                if not stop_result:
                    return
                stopped, errors = stop_result
                if errors:
                    self.notify(
                        f"Killed {len(stopped)} container(s), {len(errors)} failed",
                        severity="error",
                    )
                else:
                    self.notify(f"Killed {len(stopped)} container(s)")
                await self.refresh_tasks()
                return

        # ---------- Actions (keys + called from buttons) ----------

        async def action_edit_global_instructions(self) -> None:
            """Edit Global Instructions."""
            await self._action_edit_global_instructions()

        async def action_show_default_instructions(self) -> None:
            """Show Default Instructions."""
            await self._action_show_default_instructions()

        async def action_panic(self) -> None:
            """Emergency panic: arm the button (or fire if already armed)."""
            try:
                btn = self.query_one("#panic-button", PanicButton)
            except Exception:
                self.notify("Panic button not found — resize terminal?")
                return
            if btn._armed:
                btn.fire()
            else:
                btn.arm()

        async def on_panic_button_fired(self, _event: PanicButton.Fired) -> None:
            """Handle the panic button completing its arm-then-fire sequence."""
            await self._execute_panic_phase1()

        async def _execute_panic_phase1(self) -> None:
            """Launch Phase 1 panic: shields up, stop proxy and gate."""
            self.run_worker(
                lambda: execute_panic(stop_containers=False),
                name="panic-lockdown",
                group="panic",
                thread=True,
                exit_on_error=False,
            )

        async def _on_panic_stop_confirmed(self, confirmed: bool | None) -> None:
            """Handle the container-stop confirmation after panic lockdown."""
            if not confirmed:
                pr = getattr(self, "_last_panic_result", None)
                if pr and pr.shield_bypassed:
                    self.notify("Containers left running (shields BYPASSED — no firewall)")
                elif pr and pr.shield_errors:
                    self.notify("Containers left running (some shields failed)")
                else:
                    self.notify("Containers left running (shields are up)")
                return
            self.run_worker(
                panic_stop_containers,
                name="panic-stop-containers",
                group="panic-stop",
                thread=True,
                exit_on_error=False,
            )

        def action_confirm_quit(self) -> None:
            """Guard the main-screen ``q``: ask for a second ``q`` before quitting.

            A stray ``q`` on the main screen would otherwise tear down the
            whole TUI.  Sub-screens bind their own ``q`` to dismiss, so this
            only intercepts the top-level quit; the command-palette "Quit"
            still calls [`action_quit`][terok.tui.app.TerokTUI.action_quit]
            directly.

            When quitting would close a terok-managed tmux window and drop
            the user into one of the remaining task windows, the guard is
            the tmux-aware [`TmuxQuitScreen`][terok.tui.screens.TmuxQuitScreen]
            instead — ``qq`` then detaches back to the user's terminal.
            """
            if other_windows := tmux_session.quit_lands_in_other_window():
                self.push_screen(TmuxQuitScreen(other_windows), self._on_tmux_quit_choice)
                return
            self.push_screen(QuitConfirmScreen(), self._on_quit_confirmed)

        async def _on_quit_confirmed(self, should_quit: bool | None) -> None:
            """Quit the TUI when the operator pressed ``q`` a second time."""
            if should_quit:
                await self.action_quit()

        async def _on_tmux_quit_choice(self, choice: str | None) -> None:
            """Apply the tmux-aware quit choice: detach to the terminal or hop windows."""
            if choice == "detach":
                tmux_session.detach_client()
                await self.action_quit()
            elif choice == "next":
                await self.action_quit()

        async def action_quit(self, *, restart: bool = False) -> None:
            """Exit the TUI cleanly.

            If real-work workers (task delete, image build, etc.) are
            still in flight after the pollers have been torn down, surface
            them in Textual's exit message so the user knows the terminal
            isn't hung — the process is just waiting for the threads to
            drain before returning the prompt.

            With ``restart=True`` the app exits with
            ``_RESTART_EXIT_RESULT`` so ``_run_tui`` re-execs the process
            in place (same terminal, same tmux window) to pick up a terok
            version that was installed while the TUI was running.
            """
            self._stop_upstream_polling()
            self._stop_container_status_polling()
            self._stop_vault_watcher()
            if self._askpass_service is not None:
                await self._askpass_service.stop()

            # When the window dies with us and the client lands in another
            # tmux window, leave a status-line breadcrumb there.  A restart
            # keeps the window alive, so no hint.  Threaded but awaited:
            # the hint must be issued before the process (and with it the
            # window) dies, yet a hung tmux server should freeze at most
            # this coroutine, not the whole UI.
            if not restart:
                import asyncio

                await asyncio.to_thread(tmux_session.flash_exit_hint)

            exit_kwargs: dict[str, Any] = {"result": _RESTART_EXIT_RESULT} if restart else {}
            pending = [
                w for w in self.workers if w.state in (WorkerState.PENDING, WorkerState.RUNNING)
            ]
            if pending:
                groups = sorted({w.group for w in pending if w.group})
                suffix = f" ({', '.join(groups)})" if groups else ""
                self.exit(
                    **exit_kwargs,
                    message=(
                        f"Exiting. Waiting for {len(pending)} background task(s) to finish{suffix}."
                    ),
                )
            else:
                self.exit(**exit_kwargs)

        def _check_for_update(self) -> None:
            """Kick off the on-disk version probe on a worker thread."""
            self.run_worker(
                self._probe_installed_version,
                name="update-check",
                group="update-check",
                thread=True,
                exclusive=True,
                exit_on_error=False,
            )

        def on_app_focus(self) -> None:
            """Probe for a landed upgrade the moment attention returns to the TUI.

            Fires on terminal focus-in: re-attaching the tmux session,
            switching back to the TUI's window or pane, refocusing the
            terminal.  Whoever just upgraded terok in another window and
            came back should meet the restart offer right away, not
            after the idle interval.  The probe worker is exclusive, so
            a burst of focus flips collapses into one probe.
            """
            if not self.is_web:
                self._check_for_update()

        def _probe_installed_version(self) -> None:
            """Compare the on-disk version with the running one (worker thread).

            Any difference means the running code is stale — upgrades and
            downgrades alike — so direction is deliberately not compared.
            """
            installed = _installed_dist_version()
            running, _ = _get_version_info()
            if not installed or installed in (running, self._update_offered_version):
                return
            self.call_from_thread(self._offer_update_restart, running, installed)

        def _offer_update_restart(self, running: str, installed: str) -> None:
            """Push the restart offer for a freshly installed terok version."""
            self._update_offered_version = installed
            self.push_screen(
                UpdateRestartScreen(
                    running=_short_version(running), installed=_short_version(installed)
                ),
                self._on_update_restart_choice,
            )

        async def _on_update_restart_choice(self, restart: bool | None) -> None:
            """Restart the TUI in place when the operator accepted the offer."""
            if restart:
                await self.action_quit(restart=True)

        async def ensure_askpass_service(self) -> AskpassService:
            """Return the running [`AskpassService`][terok.tui.app.AskpassService], starting it on first use.

            Idempotent.  The first call from a ``use_personal_ssh`` project
            binds the socket; subsequent calls reuse the same service for
            the lifetime of the TUI.  ``action_quit`` tears it down.
            """
            if self._askpass_service is None:
                self._askpass_service = AskpassService(self)
            await self._askpass_service.start()
            return self._askpass_service

        async def action_show_project_actions(self) -> None:
            """Show detail screen with project info and actions."""
            if not self.current_project_name:
                self.notify("No project selected.")
                return
            if self.current_project_name in self._broken_by_id:
                bp = self._broken_by_id[self.current_project_name]
                self.notify(
                    f"Cannot act on broken project '{bp.name}'. Fix {bp.config_path} first.",
                    severity="warning",
                    timeout=10,
                )
                return
            project = self._projects_by_id.get(self.current_project_name)
            if not project:
                self.notify("Project data not loaded yet.")
                return
            await self.push_screen(
                ProjectDetailsScreen(
                    project,
                    self._last_project_state,
                    self._last_task_count,
                    self._staleness_info,
                ),
                self._on_project_action_screen_result,
            )

        async def action_show_task_actions(self) -> None:
            """Show detail screen with task info and actions."""
            if not self.current_project_name:
                self.notify("No project selected.")
                return
            try:
                task_list = self.query_one("#task-list", TaskList)
                has_tasks = bool(task_list.tasks)
            except Exception:
                has_tasks = False
            await self.push_screen(
                TaskDetailsScreen(
                    self.current_task,
                    has_tasks,
                    self.current_project_name,
                    self._last_image_old,
                ),
                self._on_task_action_screen_result,
            )

        async def _on_project_action_screen_result(self, result: str | None) -> None:
            """Handle result from project actions screen."""
            if result:
                await self._handle_project_action(result)

        async def _on_task_action_screen_result(self, result: str | None) -> None:
            """Handle result from task actions screen."""
            if result:
                await self._handle_task_action(result)

        async def _handle_project_action(self, action: str) -> None:
            """Handle project actions."""
            if action.startswith("auth_"):
                await self._action_auth(action[5:])
                return
            handler = PROJECT_ACTION_HANDLERS.get(action)
            if handler:
                result = getattr(self, handler)()
                if inspect.isawaitable(result):
                    await result

        async def _handle_task_action(self, action: str) -> None:
            """Handle task actions."""
            handler = TASK_ACTION_HANDLERS.get(action)
            if handler:
                result = getattr(self, handler)()
                if inspect.isawaitable(result):
                    await result

        # ---------- Command palette ----------

        def get_system_commands(self, screen: Any) -> "Iterator[Any]":
            """Add gate, shield, and proxy management to the command palette."""
            from textual.app import SystemCommand

            # Textual's built-in "Keys" command toggles the key panel; rename it
            # so it doesn't read as a peer of "SSH Key Routing" in the palette.
            for command in super().get_system_commands(screen):
                if command.title == "Keys":
                    yield command._replace(title="Keyboard shortcuts")
                else:
                    yield command
            yield SystemCommand(
                "Run terok setup",
                "Install or re-apply host shield hooks + the per-container supervisor + desktop entry",
                self.action_run_setup,
            )
            yield SystemCommand(
                "Console output",
                "View live and captured logs from dispatched actions (builds, gate/vault ops)",
                self.action_show_console_output,
            )
            yield SystemCommand(
                "Shield Status",
                "View shield environment and install hooks",
                self.action_show_shield,
            )
            yield SystemCommand(
                "Vault",
                "Manage vault status and operations",
                self.action_show_vault,
            )
            yield SystemCommand(
                "SSH Key Routing",
                "Wire vault SSH keys to projects on a routing matrix; mint and delete keys",
                self.action_show_key_routing,
            )
            yield SystemCommand(
                "PANIC — Emergency Kill Switch",
                "Cut all resource access immediately (shields, vault, gate)",
                self.action_panic,
            )
            yield SystemCommand(
                "Shield Clearance (live)",
                "Monitor blocked connections and send verdicts via D-Bus",
                self.action_show_clearance,
            )
            yield SystemCommand(
                "Authenticate agents and tools",
                "Run the host-wide auth flow for an agent or tool — no project required",
                self.action_authenticate,
            )
            yield SystemCommand(
                "Set default agents",
                "Pick the agent roster baked into L1 images by default (image.agents in config.yml)",
                self.action_configure_default_agents,
            )

        def action_show_console_output(self) -> None:
            """Open the console-output list — every dispatched action's log this session."""
            from .console_output_screen import ConsoleOutputScreen

            self.push_screen(ConsoleOutputScreen(self.console_logs))

        async def action_authenticate(self) -> None:
            """Open the host-wide ``Authenticate agents and tools`` modal.

            Reuses [`AuthActionsScreen`][terok.tui.screens.AuthActionsScreen], but the result handler
            forces a host-wide auth flow (``project_name=None``) regardless
            of what's selected in the main pane — the per-project entry
            lives on the project-details screen.
            """
            from .screens import AuthActionsScreen

            await self.push_screen(AuthActionsScreen(), self._on_authenticate_result)

        async def _on_authenticate_result(self, result: str | None) -> None:
            """Route the host-wide auth modal's selection.

            ``auth_<provider>`` lands in `_action_auth_host_wide`
            (forced ``project_name=None``); ``import_opencode_config``
            shares the project-screen handler since the import is
            project-agnostic anyway.
            """
            if not result:
                return
            if result.startswith("auth_"):
                await self._action_auth_host_wide(result[5:])
                return
            if result == "import_opencode_config":
                await self._action_import_opencode_config()

        async def action_configure_default_agents(self) -> None:
            """Open the shared agents modal scoped to the global default.

            On dismissal the selection is written to ``image.agents`` in
            ``~/.config/terok/config.yml``.  Cancel dismisses without
            touching the file.
            """
            from terok.lib.integrations.executor import ExecutorConfigView

            from .agents_screen import AgentsSelectScreen

            current = ExecutorConfigView.image_agents()
            await self.push_screen(
                AgentsSelectScreen(
                    initial=current or "",
                    title="Default agents (config.yml)",
                ),
                self._on_default_agents_result,
            )

        async def _on_default_agents_result(self, result: str | None) -> None:
            """Write the chosen selection to the global config; ``None`` = no change."""
            if result is None:
                return
            from terok.lib.api.agents import ExecutorConfigView

            path = ExecutorConfigView.set_image_agents(result)
            self.notify(
                f"Wrote image.agents = {result!r} to {path}",
                severity="information",
            )

        async def action_show_shield(self) -> None:
            """Open the shield environment screen."""
            await self.push_screen(
                ShieldScreen(self._last_shield_env),
                self._on_shield_action_result,
            )

        async def _on_shield_action_result(self, result: str | None) -> None:
            """Handle result from shield screen."""
            if not result:
                return
            handler = SHIELD_ACTION_HANDLERS.get(result)
            if handler:
                await getattr(self, handler)()

        async def action_show_key_routing(self) -> None:
            """Open the SSH key ↔ project routing matrix."""
            from .key_routing_screen import KeyRoutingScreen

            await self.push_screen(KeyRoutingScreen())

        async def action_show_vault(self) -> None:
            """Open the vault management screen."""
            from terok.lib.api.vault import load_vault_status

            try:
                self._last_vault_status = load_vault_status()
            except Exception:
                self._last_vault_status = None
            await self.push_screen(
                VaultScreen(self._last_vault_status),
                self._on_vault_action_result,
            )

        async def _on_vault_action_result(self, result: str | None) -> None:
            """Handle result from vault screen."""
            if not result:
                return
            handler = VAULT_ACTION_HANDLERS.get(result)
            if handler:
                await getattr(self, handler)()

        def _maybe_warn_recovery_unconfirmed(self) -> None:
            """One-shot startup notification for the recovery-key warnings.

            The pill in the status bar carries the same signal on every
            refresh; this lifts it to a notification once per process
            so a user who just finished setup (and hasn't yet learned
            to look at the pill) gets a louder reminder.  Suppressed
            silently on a locked vault — the unlock prompt is already
            pulling the operator's attention.

            Text and severity come from the snapshot's shared warning
            catalog, so the CLI, the pill, and this notification can
            never drift apart; only the TUI affordance line ("open the
            Vault screen → Reveal") is added here.
            """
            from terok.lib.api.vault import VaultState, VaultWarningKind

            if getattr(self, "_recovery_warning_shown", False):
                return
            self._recovery_warning_shown = True
            vault_status = getattr(self, "_last_vault_status", None)
            if vault_status is None or vault_status.state is not VaultState.UNLOCKED:
                return
            recovery_kinds = (
                VaultWarningKind.RECOVERY_VOLATILE,
                VaultWarningKind.RECOVERY_UNCONFIRMED,
            )
            warning = next((w for w in vault_status.warnings if w.kind in recovery_kinds), None)
            if warning is None:
                return
            self.notify(
                f"{warning.message.capitalize()}.  Open the Vault screen → Reveal"
                " and save the key off-host.",
                title=f"Vault: {warning.brief}",
                severity="error" if warning.severity == "error" else "warning",
                timeout=30 if warning.severity == "error" else 20,
            )

        def _start_vault_watcher(self) -> None:
            """Arm an inotify watch on the session-unlock file's directory.

            A `terok vault unlock` / `lock` in another terminal changes
            the pill's truth, but the pill only re-probed on mount and
            after TUI-initiated actions — the operator stared at a stale
            "LOCKED" until restart (issue #1070's first symptom).  Reuses
            [`TaskWatcher`][terok.tui.task_watcher.TaskWatcher] on the
            *runtime* dir only: every chain-mutating verb touches the
            session file, while the vault/DB dir is deliberately NOT
            watched — the refresh itself opens the DB, whose WAL writes
            would fire the watch and self-trigger a refresh loop.
            Best-effort: without inotify the existing mount/action
            refreshes still apply.
            """
            import asyncio

            from .task_watcher import TaskWatcher

            try:
                watcher = TaskWatcher()
                session_dir = SandboxConfig().vault_passphrase_file.parent
                # The dir may not exist before the first unlock; create it
                # so the watch can arm now rather than never.
                session_dir.mkdir(parents=True, exist_ok=True)
                if not watcher.start([session_dir]):
                    return
            except Exception:  # noqa: BLE001 — watch is best-effort enrichment
                return
            try:
                asyncio.get_running_loop().add_reader(
                    watcher.fileno, self._on_vault_session_dir_changed
                )
            except (RuntimeError, ValueError, OSError, NotImplementedError):
                watcher.stop()
                return
            self._vault_watcher = watcher

        def _on_vault_session_dir_changed(self) -> None:
            """Drain inotify events, then debounce a single pill refresh."""
            if self._vault_watcher is None or not self._vault_watcher.drain():
                return
            if self._vault_watch_debounce is not None:
                self._vault_watch_debounce.stop()
            self._vault_watch_debounce = self.set_timer(0.5, self._on_vault_watch_fired)

        async def _on_vault_watch_fired(self) -> None:
            """Debounce landing point: re-probe the vault and repaint the pill."""
            self._vault_watch_debounce = None
            await self._refresh_vault_status()

        def _stop_vault_watcher(self) -> None:
            """Detach and close the vault inotify watch and any pending debounce."""
            if self._vault_watch_debounce is not None:
                self._vault_watch_debounce.stop()
                self._vault_watch_debounce = None
            if self._vault_watcher is None:
                return
            import asyncio

            try:
                asyncio.get_running_loop().remove_reader(self._vault_watcher.fileno)
            except (RuntimeError, ValueError, OSError):
                pass
            self._vault_watcher.stop()
            self._vault_watcher = None

        async def _refresh_vault_status(self, *, push_modal_if_locked: bool = False) -> None:
            """Read a fresh snapshot, update the pill, optionally push the unlock modal."""
            from terok.lib.api.vault import VaultState, load_vault_status

            try:
                self._last_vault_status = load_vault_status()
            except Exception:
                self._last_vault_status = None

            self._render_status_pill(self._last_vault_status)

            # Only a genuinely LOCKED vault gets the unlock prompt.  An
            # UNPROVISIONED one has nothing to unlock — the modal would
            # accept any string and silently key the future vault to it
            # on the reboot-volatile session tier.  First-time
            # provisioning belongs to the chooser + create + reveal
            # flow, which runs right after this probe on startup; the
            # palette unlock action routes there too.
            if (
                push_modal_if_locked
                and self._last_vault_status is not None
                and self._last_vault_status.state is VaultState.LOCKED
            ):
                await self.push_screen(VaultUnlockModal(), self._on_vault_unlock_result)

        def _render_status_pill(self, status: "VaultStatus | None") -> None:
            """Update the bottom StatusBar with a short vault-state pill.

            The pill is pure rendering: the state comes from the
            snapshot's classifier and the "visible until acted on"
            annotations are the shared warning catalog's ``brief``
            forms — the same facts the CLI's ``vault status`` and the
            startup notification present, worded once sandbox-side.
            """
            from terok.lib.api.vault import VaultState

            try:
                bar = self.query_one("#status-bar", StatusBar)
            except NoMatches:
                # Compose hasn't mounted the bar yet — silent skip; the
                # next ``_refresh_vault_status`` will land the message.
                return
            if status is None:
                bar.set_message("")
                return
            if status.state is VaultState.UNPROVISIONED:
                bar.set_message("Vault: not set up yet — run terok setup from the command palette")
                return
            if status.state is VaultState.ERROR:
                bar.set_message(f"Vault: ERROR — {status.db_error}")
                return
            if status.state is VaultState.LOCKED:
                # The reason distinguishes "no passphrase" from "wrong
                # passphrase" from "broken tier" — without it the operator
                # can't tell whether to type harder or fix a seal.
                reason = f" ({status.lock_reason})" if status.lock_reason else ""
                bar.set_message(
                    f"Vault: LOCKED{reason} — open Vault from the command palette to unlock"
                )
                return
            suffix = "".join(
                f" — {warning.brief}" for warning in status.warnings if warning.severity != "info"
            )
            bar.set_message(f"Vault: unlocked ({status.source}){suffix}")

        async def _on_vault_unlock_result(self, passphrase: "str | None") -> None:
            """Validate the typed passphrase and land it on the session-unlock tier.

            Funnels through sandbox's ``provision_session_passphrase`` —
            the same validated writer the CLI uses — so a wrong entry is
            rejected with a clear message instead of being written,
            reported as success, and leaving the pill on "locked" with
            no explanation (the failure mode behind issue #1070).  After
            a successful write, re-probe so the pill reflects the new
            source and the locked state clears.
            """
            if not passphrase:
                return

            from terok.lib.api.vault import WrongPassphraseError, provision_session_passphrase

            cfg = SandboxConfig()
            try:
                result = provision_session_passphrase(cfg, passphrase)
            except WrongPassphraseError:
                self.notify(
                    "That passphrase does not open the credentials DB — nothing was"
                    " written.  Wrong key, or a DB from another install.",
                    severity="error",
                    timeout=10,
                )
                return
            except OSError as exc:
                self.notify(
                    f"Failed to write session-unlock file: {exc}",
                    severity="error",
                    timeout=10,
                )
                return
            except Exception as exc:  # noqa: BLE001 — e.g. legacy plaintext DB; surface verbatim
                self.notify(str(exc), severity="error", timeout=10)
                return
            if not result.written:
                # A durable tier (systemd-creds / keyring / config) already
                # unlocks the vault, so a session file would only shadow it —
                # exactly the residue this guard prevents.  Inform, don't write.
                self.notify(
                    f"Vault already auto-unlocks via {result.shadowed_durable} — no session"
                    " passphrase needed.",
                    severity="information",
                    timeout=8,
                )
                return
            self.notify("Vault unlocked for this session.", severity="information", timeout=5)
            await self._refresh_vault_status()

        async def action_show_clearance(self) -> None:
            """Open the live clearance screen for D-Bus shield notifications."""
            from .clearance_screen import ClearanceScreen

            await self.push_screen(ClearanceScreen())

        async def action_show_clearance_from_main(self) -> None:
            """Open the clearance screen from the main screen."""
            await self.action_show_clearance()

    def _run_tui(restart_flags: tuple[str, ...] = ()) -> None:
        """Run the TUI; when it exits requesting a restart, re-exec in place.

        The re-exec replaces this process with a freshly resolved
        ``terok-tui`` entry point (new code, same terminal, same tmux
        window when running under one).  *restart_flags* carries the CLI
        flags to preserve — rebuilt by ``_restart_flags`` from parsed
        values, never echoed from raw ``sys.argv``.
        """
        import shutil

        # Must precede the app: tmux honours a pane's focus-reporting
        # request only if the option is on when Textual makes it.
        tmux_session.enable_focus_events()
        result = TerokTUI().run()
        if result != _RESTART_EXIT_RESULT:
            return
        exe = shutil.which("terok-tui")
        if not exe:
            print("terok-tui not found on PATH — restart it manually to pick up the update.")
            return
        try:
            os.execv(exe, [exe, *restart_flags])  # nosec B606 — resolved entry point, literal flags
        except OSError:
            # The entry point vanished or lost its exec bit between the
            # which() above and here — an upgrade in flight can do that.
            print(f"Could not restart {exe} — start terok-tui again manually.")

    def _launch_in_tmux(force_new: bool = False, restart_flags: tuple[str, ...] = ()) -> None:
        """Launch the TUI inside a managed tmux session.

        If already inside tmux, just run the TUI directly.  Otherwise verify
        that tmux is installed and exec into it with the terok host config
        (blue status bar, usage hints).  Exits with an actionable error
        message if tmux is not found on ``$PATH``.

        By default a second ``terok --tmux`` resumes the shared ``terok``
        session: the client lands on the window stamped ``@terok-main``,
        and when no stamped window is left (the TUI was quit while task
        windows kept the session alive) a fresh TUI window is spawned in
        the original spot — first in the window list — before attaching.
        Pass ``force_new=True`` (``--new-session``) to spin up a
        fresh, tmux-named session alongside the existing one instead.

        Sessions are created with the ``TEROK_TMUX=1`` session environment
        marker so everything inside can tell a terok-managed tmux apart
        from the user's own — on a tmux too old for ``new-session -e``
        (< 3.2) the marker is skipped and the tmux niceties stay off.
        """
        if os.environ.get("TMUX"):
            # Already inside tmux — no double-wrap
            _run_tui(restart_flags)
            return

        import shutil

        if not shutil.which("tmux"):
            print(
                "Error: tmux is not installed.\n"
                "Install it (e.g. 'apt install tmux' or 'brew install tmux') "
                "and try again,\nor run 'terok-tui' without --tmux.",
                file=sys.stderr,
            )
            sys.exit(1)

        if not force_new and tmux_session.session_exists():
            # Resume: land the client on the TUI's stamped window rather
            # than wherever the session last was; revive the TUI as the
            # session's first window when none is stamped (window ids are
            # stable handles, indexes are not — the host config renumbers
            # windows).
            session = f"={tmux_session.SESSION_NAME}"
            main_window = tmux_session.find_main_window()
            if main_window:
                land_args = ["select-window", "-t", main_window]
            else:
                revive = tmux_session.revive_window_args()
                land_args = ["new-window", *revive, "-n", "terok", "terok-tui"]
            os.execvp(  # nosec B606 B607 — tmux from PATH, argv of fixed verbs
                "tmux", ["tmux", *land_args, ";", "attach-session", "-t", session]
            )

        from importlib import resources as _res

        tmux_conf = _res.files("terok") / "resources" / "tmux" / "host-tmux.conf"
        # Materialise the resource to a real file path for tmux -f.
        # Note: os.execvp replaces this process so the context manager's
        # __exit__ never runs.  This is fine — tmux reads the config file
        # at startup, and OS process cleanup handles any temp resources.
        # ``-A -s terok`` still guards the race where another ``terok
        # --tmux`` creates the session between the check above and this
        # exec; ``--new-session`` drops the name so tmux auto-assigns one
        # for a parallel session.
        session_args = (
            ["new-session"]
            if force_new
            else ["new-session", "-A", "-s", tmux_session.SESSION_NAME, "-n", "terok"]
        )
        marker_args = tmux_session.session_marker_args()
        with _res.as_file(tmux_conf) as conf_path:
            os.execvp(
                "tmux",
                ["tmux", "-f", str(conf_path), *session_args, *marker_args, "terok-tui"],
            )

    import argparse

    def _build_arg_parser() -> argparse.ArgumentParser:
        """Build the ``terok-tui`` argument parser.

        ``--tmux`` / ``--no-tmux`` share a single tri-state destination:
        ``None`` means "user passed neither flag — fall back to config".
        ``store_true``/``store_false`` would otherwise leave a bool there
        and mask the fallback in [`main`][terok.tui.app.main].
        """
        parser = argparse.ArgumentParser(prog="terok-tui")
        # Mutually exclusive: passing both raises before parse_args returns.
        # ``set_defaults(tmux=None)`` keeps the destination tri-state when
        # neither flag is passed so ``main`` can defer to the config setting.
        tmux_group = parser.add_mutually_exclusive_group()
        tmux_group.add_argument(
            "--tmux",
            action="store_true",
            help="Launch TUI inside a managed tmux session",
        )
        tmux_group.add_argument(
            "--no-tmux",
            dest="tmux",
            action="store_false",
            help="Launch TUI directly in current terminal (default if not configured)",
        )
        parser.set_defaults(tmux=None)
        parser.add_argument(
            "--new-session",
            action="store_true",
            default=False,
            help=(
                "Start a fresh tmux session instead of attaching to the running "
                "'terok' one (only meaningful in tmux mode)"
            ),
        )
        parser.add_argument(
            "--experimental",
            action="store_true",
            default=False,
            help="Enable experimental features (e.g. web tasks)",
        )
        parser.add_argument(
            "--no-emoji",
            action="store_true",
            default=False,
            help="Replace emojis with text labels (e.g. [gate] instead of \U0001f6aa)",
        )
        return parser

    def _restart_flags(args: argparse.Namespace) -> tuple[str, ...]:
        """Rebuild the CLI flags a restart re-exec should carry.

        Reconstructed from the *parsed* values rather than echoed from raw
        ``sys.argv``, so a restarted process can only ever receive flags
        this parser understands.
        """
        return tuple(
            flag
            for flag, wanted in (
                ("--tmux", args.tmux is True),
                ("--no-tmux", args.tmux is False),
                ("--new-session", args.new_session),
                ("--experimental", args.experimental),
                ("--no-emoji", args.no_emoji),
            )
            if wanted
        )

    def main() -> None:
        """CLI entry-point for launching the terok TUI.

        Three-way control of tmux wrapping:

        - ``--tmux`` forces the TUI into a managed host tmux session
          (blue status bar, login windows as extra tmux windows).
        - ``--no-tmux`` forces the TUI to run directly in the current
          terminal.
        - When neither flag is passed, the global config setting
          ``tui.default_tmux`` decides (defaults to ``False``).

        In tmux mode the TUI attaches to the shared ``terok`` session when one
        is already running; ``--new-session`` opts out and starts a parallel,
        tmux-named session instead.
        """
        from terok.lib.core.config import declare_setup_invocation

        declare_setup_invocation()
        args = _build_arg_parser().parse_args()
        set_experimental(args.experimental)

        if args.no_emoji:
            from ..lib.util.emoji import set_emoji_enabled

            set_emoji_enabled(False)

        # Determine tmux mode: explicit flag > config default > False
        # Web mode (textual-serve) is never wrapped in tmux.
        use_tmux = (
            args.tmux
            if hasattr(args, "tmux") and args.tmux is not None
            else get_config().tui_default_tmux
        )

        from .shell_launch import is_web_mode

        restart_flags = _restart_flags(args)
        if use_tmux and not is_web_mode():
            _launch_in_tmux(force_new=args.new_session, restart_flags=restart_flags)
            return
        _run_tui(restart_flags)

else:

    def main() -> None:
        """Print an error message when Textual is not installed and exit."""
        print(
            "terok TUI requires the 'textual' package, but it is not installed.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    enable_pycharm_debugger()
    main()
