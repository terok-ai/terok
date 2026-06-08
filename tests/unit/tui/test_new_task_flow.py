# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the new task creation and launch workflow (#296 + #446)."""

from __future__ import annotations

import asyncio
import types
from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest

from tests.unit.tui.tui_test_helpers import import_app, import_screens


def run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# TaskCreateScreen
# ---------------------------------------------------------------------------


class TestTaskCreateScreen:
    """Tests for the TaskCreateScreen modal."""

    def test_construction_default_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="my-task")
        assert screen._default_name == "my-task"

    def test_construction_empty_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen()
        assert screen._default_name == ""

    def test_cancel_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="t")
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)

    def test_button_cancel_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="t")
        screen.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "btn-cancel"
        screen.on_button_pressed(event)
        screen.dismiss.assert_called_once_with(None)

    def test_submit_validates_and_sanitizes_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="fallback")
        screen.dismiss = mock.Mock()
        screen.notify = mock.Mock()

        # Stub query_one to return mock Input with valid name
        mock_input = mock.Mock()
        mock_input.value = "  My Task  "
        screen.query_one = mock.Mock(return_value=mock_input)

        screen._submit("cli")
        screen.dismiss.assert_called_once()
        name, mode = screen.dismiss.call_args[0][0]
        assert mode == "cli"
        assert name == "my-task"  # sanitized

    def test_submit_rejects_empty_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="")
        screen.dismiss = mock.Mock()
        screen.notify = mock.Mock()

        mock_input = mock.Mock()
        mock_input.value = ""
        screen.query_one = mock.Mock(return_value=mock_input)

        screen._submit("cli")
        screen.dismiss.assert_not_called()
        screen.notify.assert_called_once()

    def test_submit_falls_back_to_default_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="fallback-name")
        screen.dismiss = mock.Mock()
        screen.notify = mock.Mock()

        mock_input = mock.Mock()
        mock_input.value = ""
        screen.query_one = mock.Mock(return_value=mock_input)

        screen._submit("toad")
        screen.dismiss.assert_called_once()
        name, mode = screen.dismiss.call_args[0][0]
        assert name == "fallback-name"
        assert mode == "toad"

    def test_option_list_selection_submits(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="t")
        screen._submit = mock.Mock()

        event = mock.Mock()
        event.option_id = "unattended"
        screen.on_option_list_option_selected(event)
        screen._submit.assert_called_once_with("unattended")


# ---------------------------------------------------------------------------
# TaskLaunchScreen
# ---------------------------------------------------------------------------


class TestTaskLaunchScreen:
    """Tests for the TaskLaunchScreen modal."""

    def test_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="terok-p-cli-1",
            project_name="p",
            task_id="1",
            task_name="fix-bug",
            default_shell="claude",
        )
        assert screen._container_name == "terok-p-cli-1"
        assert screen._project_name == "p"
        assert screen._task_id == "1"
        assert screen._task_name == "fix-bug"
        assert screen._default_shell == "claude"
        assert not screen._container_ready

    def test_construction_default_bash(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        assert screen._default_shell == "bash"
        assert screen._task_name == "1"  # falls back to task_id

    def test_dismiss_returns_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen.dismiss = mock.Mock()
        screen.action_dismiss_screen()
        screen.dismiss.assert_called_once_with(None)

    def test_dismiss_via_button(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "btn-dismiss"
        screen.on_button_pressed(event)
        screen.dismiss.assert_called_once_with(None)

    def test_console_entry_stored_when_provided(self) -> None:
        """The background-container-start entry is held for the Show-log button."""
        screens, _ = import_screens()
        entry = mock.Mock()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", console_entry=entry
        )
        assert screen._console_entry is entry

    def test_console_entry_defaults_to_none(self) -> None:
        """Without a console_entry there is nothing to foreground — Show-log is omitted."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        assert screen._console_entry is None

    def test_show_log_button_opens_worker_log_view(self) -> None:
        """The Show-log button foregrounds the entry's WorkerLogScreen — and only then."""
        screens, _ = import_screens()
        entry = mock.Mock()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", console_entry=entry
        )
        screen.app = mock.Mock()
        event = mock.Mock()
        event.button.id = "btn-show-log"
        screen.on_button_pressed(event)
        # A screen was pushed (the WorkerLogScreen view over the entry).
        screen.app.push_screen.assert_called_once()

    def test_do_login_returns_agent_and_prompt(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", task_name="fix-bug"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "claude"
        mock_textarea = mock.Mock()
        mock_textarea.text = "fix the bug"

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        screen._do_login()
        screen.dismiss.assert_called_once_with(("p", "1", "fix-bug", "c", "claude", "fix the bug"))

    def test_do_login_bash_keeps_prompt(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", task_name="my-task"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "bash"
        mock_textarea = mock.Mock()
        mock_textarea.text = "scribble for the agent"

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        screen._do_login()
        screen.dismiss.assert_called_once_with(
            ("p", "1", "my-task", "c", "bash", "scribble for the agent")
        )

    def test_do_login_bash_empty_prompt_is_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", task_name="my-task"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "bash"
        mock_textarea = mock.Mock()
        mock_textarea.text = "   "

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        screen._do_login()
        screen.dismiss.assert_called_once_with(("p", "1", "my-task", "c", "bash", None))

    def test_login_button_blocked_when_not_ready(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen._do_login = mock.Mock()

        assert not screen._container_ready

        # Simulate Enter in the prompt input — should not login
        event = mock.Mock()
        screen.on_input_submitted(event)
        screen._do_login.assert_not_called()

    def test_login_button_allowed_when_ready(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen._do_login = mock.Mock()
        screen._container_ready = True

        event = mock.Mock()
        screen.on_input_submitted(event)
        screen._do_login.assert_called_once()

    def test_on_key_ctrl_enter_is_owned_by_the_widget(self) -> None:
        """The screen ignores Ctrl+Enter — newline insertion lives in the widget.

        ``_SubmittablePromptArea`` inserts the newline and stops the event, so
        Ctrl+Enter never reaches the screen's ``on_key``; if it somehow does,
        the screen must treat it as a no-op (no insert, no submit).
        """
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")

        mock_textarea = mock.Mock()
        mock_textarea.text = "line1"
        mock_textarea.has_focus = True
        mock_textarea.insert = mock.Mock()

        mock_select = mock.Mock()

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        # Simulate Ctrl+Enter
        event = mock.Mock()
        event.key = "ctrl+enter"
        screen.on_key(event)

        # The screen does nothing — the widget already handled the newline.
        mock_textarea.insert.assert_not_called()
        event.stop.assert_not_called()

    def test_on_key_enter_submits_when_ready_and_focused(self) -> None:
        """Enter (without Ctrl) submits when container ready and prompt has focus."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen._container_ready = True
        screen._do_login = mock.Mock()

        mock_textarea = mock.Mock()
        mock_textarea.has_focus = True

        mock_select = mock.Mock()

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        # Simulate Enter (without Ctrl)
        event = mock.Mock()
        event.key = "enter"
        event.ctrl = False
        screen.on_key(event)

        screen._do_login.assert_called_once()
        event.stop.assert_called_once()

    def test_on_key_enter_noop_when_not_ready(self) -> None:
        """Enter does nothing when container is not ready, even with focus."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen._container_ready = False
        screen._do_login = mock.Mock()

        mock_textarea = mock.Mock()
        mock_textarea.has_focus = True

        mock_select = mock.Mock()

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        # Simulate Enter (without Ctrl)
        event = mock.Mock()
        event.key = "enter"
        event.ctrl = False
        screen.on_key(event)

        screen._do_login.assert_not_called()
        event.stop.assert_not_called()

    def test_on_key_enter_noop_when_not_focused(self) -> None:
        """Enter does nothing when prompt doesn't have focus, even if ready."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="1")
        screen._container_ready = True
        screen._do_login = mock.Mock()

        mock_textarea = mock.Mock()
        mock_textarea.has_focus = False  # No focus

        mock_select = mock.Mock()

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_textarea

        screen.query_one = query_one

        # Simulate Enter (without Ctrl)
        event = mock.Mock()
        event.key = "enter"
        event.ctrl = False
        screen.on_key(event)

        screen._do_login.assert_not_called()
        event.stop.assert_not_called()


class TestUnattendedPromptOnKey:
    """UnattendedPromptScreen submits on Enter only while the prompt has focus."""

    @staticmethod
    def _screen_with_focus(*, has_focus: bool):
        screens, _ = import_screens()
        screen = screens.UnattendedPromptScreen()
        screen._submit = mock.Mock()
        area = mock.Mock()
        area.has_focus = has_focus
        screen.query_one = lambda *a, **k: area
        return screen

    def test_enter_submits_when_prompt_focused(self) -> None:
        """Enter (bubbled from the prompt area) runs the unattended prompt."""
        screen = self._screen_with_focus(has_focus=True)
        event = mock.Mock()
        event.key = "enter"
        screen.on_key(event)
        screen._submit.assert_called_once()
        event.stop.assert_called_once()

    def test_enter_noop_when_prompt_not_focused(self) -> None:
        """Enter elsewhere (e.g. on a button) is left for the default handler."""
        screen = self._screen_with_focus(has_focus=False)
        event = mock.Mock()
        event.key = "enter"
        screen.on_key(event)
        screen._submit.assert_not_called()
        event.stop.assert_not_called()


# ---------------------------------------------------------------------------
# TaskLaunchScreen — lazy agent dropdown
# ---------------------------------------------------------------------------


class TestTaskLaunchScreenLazyAgents:
    """Tests for the loading → loaded transition on the agent Select."""

    def test_build_agent_choices_loading_returns_bash_only(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", installed=None
        )
        assert screen._build_agent_choices() == [("bash", "bash")]

    def test_build_agent_choices_loaded_prepends_bash_to_providers(self) -> None:
        from terok.lib.integrations.executor import AGENTS

        screens, _ = import_screens()
        # Filter to a single known provider so the assertion is stable.
        target = next(iter(AGENTS))
        screen = screens.TaskLaunchScreen(
            container_name="c",
            project_name="p",
            task_id="1",
            installed=frozenset({target}),
        )
        choices = screen._build_agent_choices()
        assert choices[0] == ("bash", "bash")
        assert (AGENTS[target].label, target) in choices

    def test_set_installed_populates_select_and_restores_default(self) -> None:
        from terok.lib.integrations.executor import AGENTS

        screens, _ = import_screens()
        target = next(iter(AGENTS))
        screen = screens.TaskLaunchScreen(
            container_name="c",
            project_name="p",
            task_id="1",
            default_shell=target,
            installed=None,
        )
        mock_select = mock.Mock()
        mock_select.disabled = True
        screen.query_one = mock.Mock(return_value=mock_select)

        screen.set_installed(frozenset({target}))

        assert screen._installed == frozenset({target})
        mock_select.set_options.assert_called_once()
        passed_choices = mock_select.set_options.call_args[0][0]
        assert ("bash", "bash") in passed_choices
        assert (AGENTS[target].label, target) in passed_choices
        assert mock_select.value == target
        assert mock_select.prompt == "Select an agent"
        assert mock_select.disabled is False

    def test_set_installed_falls_back_to_bash_when_default_unavailable(self) -> None:
        """If the configured default_shell isn't installed, the dropdown still picks bash."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c",
            project_name="p",
            task_id="1",
            default_shell="not-installed-agent",
            installed=None,
        )
        mock_select = mock.Mock()
        screen.query_one = mock.Mock(return_value=mock_select)

        # Empty frozenset means "no filter" → all providers visible, but
        # ``not-installed-agent`` isn't a registered provider, so the
        # fallback should kick in.
        screen.set_installed(frozenset())

        assert mock_select.value == "bash"
        assert mock_select.disabled is False

    def test_set_installed_returns_silently_when_select_missing(self) -> None:
        """Calling set_installed before compose mounted the Select is a no-op."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", installed=None
        )
        screen.query_one = mock.Mock(side_effect=RuntimeError("not mounted"))
        # Must not raise.
        screen.set_installed(frozenset())
        # State is still updated even if the Select can't be reached.
        assert screen._installed == frozenset()


# ---------------------------------------------------------------------------
# Launch command shape — prompt travels via the on-disk file, not as a CLI arg
# ---------------------------------------------------------------------------


def _run_launch_with_prompt(agent: str, prompt: str | None) -> tuple[mock.Mock, mock.Mock]:
    """Drive _on_launch_screen_result for *agent*/*prompt* and return mocks."""
    _, app_class = import_app()
    instance = app_class()
    instance.current_project_name = "proj1"
    instance.refresh_tasks = mock.AsyncMock()
    instance._launch_terminal_session = mock.AsyncMock()

    fake_provider = mock.Mock()
    fake_provider.binary = "claude"
    save = mock.Mock()
    action_globals = app_class._on_launch_screen_result.__globals__

    with (
        mock.patch.dict(
            action_globals,
            {
                "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                "_save_initial_prompt": save,
            },
        ),
        mock.patch.dict(
            "terok_executor.provider.providers.AGENTS",
            {"claude": fake_provider},
            clear=True,
        ),
    ):
        result = ("proj1", "5", "task", "proj1-cli-5", agent, prompt)
        run(app_class._on_launch_screen_result(instance, result))

    return instance._launch_terminal_session, save


class TestLaunchCmdShape:
    """The prompt is delivered via initial-prompt.txt; the cmd never carries it."""

    def test_agent_launch_omits_prompt_from_cli(self) -> None:
        launch, save = _run_launch_with_prompt("claude", "fix 'the' bug")
        cmd = launch.call_args[0][0]
        # Wrapper consumes the file; the spawned binary is a bare invocation.
        assert cmd == ["podman", "exec", "-it", "c", "bash", "-lc", "claude"]
        save.assert_called_once_with("proj1", "5", "fix 'the' bug")

    def test_agent_launch_without_prompt(self) -> None:
        launch, save = _run_launch_with_prompt("claude", None)
        cmd = launch.call_args[0][0]
        assert cmd == ["podman", "exec", "-it", "c", "bash", "-lc", "claude"]
        save.assert_called_once_with("proj1", "5", None)

    def test_bash_launch_uses_base_cmd_directly(self) -> None:
        # Bash relies on the bashrc banner snippet (terok-executor) to display
        # the prompt — no per-launch wrapper needed.
        launch, save = _run_launch_with_prompt("bash", "first message")
        cmd = launch.call_args[0][0]
        assert cmd == ["podman", "exec", "-it", "c"]
        save.assert_called_once_with("proj1", "5", "first message")


# ---------------------------------------------------------------------------
# Config: default_shell
# ---------------------------------------------------------------------------


class TestDefaultLoginConfig:
    """Tests for the default_shell config field."""

    def test_project_model_has_default_shell(self) -> None:
        from terok.lib.core.project_model import ProjectConfig

        fields = ProjectConfig.model_fields
        assert "default_shell" in fields

    def test_project_yaml_schema_has_default_shell(self) -> None:
        from terok.lib.core.yaml_schema import RawProjectYaml

        fields = RawProjectYaml.model_fields
        assert "default_shell" in fields

    def test_global_config_schema_has_default_shell(self) -> None:
        from terok.lib.core.yaml_schema import RawGlobalConfig

        fields = RawGlobalConfig.model_fields
        assert "default_shell" in fields

    def test_project_yaml_default_shell_defaults_none(self) -> None:
        from terok.lib.core.yaml_schema import RawProjectYaml

        raw = RawProjectYaml()
        assert raw.default_shell is None

    def test_global_config_default_shell_defaults_none(self) -> None:
        from terok.lib.core.yaml_schema import RawGlobalConfig

        raw = RawGlobalConfig()
        assert raw.default_shell is None


# ---------------------------------------------------------------------------
# Worker group handlers
# ---------------------------------------------------------------------------


class TestBackgroundLaunchCompletion:
    """The on_complete callback of the background CLI / Toad container start.

    The start is dispatched as a captured ConsoleLog action; its
    on_complete callback unmarks the launch badge, refreshes the task
    list, and toasts the outcome.
    """

    @staticmethod
    def _run_and_capture_on_complete(app_class, start_method: str):
        """Run a ``_start_*_task_background`` with dispatch mocked; return (instance, on_complete)."""
        instance = app_class()
        instance.current_project_name = "proj1"
        instance._last_selected_tasks = {}
        instance._save_selection_state = mock.Mock()
        instance.notify = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        instance.refresh_tasks = mock.AsyncMock()
        instance.run_worker = mock.Mock()
        instance._mark_launching = mock.Mock()
        instance._unmark_launching = mock.Mock()

        captured: dict = {}

        def _dispatch(ref, *args, title, on_complete=None):
            captured["on_complete"] = on_complete
            return mock.Mock()

        instance.dispatch_console_action = _dispatch

        fake_project = mock.Mock()
        fake_project.default_shell = None
        action_globals = getattr(app_class, start_method).__globals__
        with mock.patch.dict(
            action_globals,
            {
                "task_new": mock.Mock(return_value="5"),
                "load_project": mock.Mock(return_value=fake_project),
                "container_name": lambda *a: "terok-proj1-cli-5",
            },
        ):
            run(getattr(app_class, start_method)(instance, "deploy"))
        return instance, captured["on_complete"]

    @staticmethod
    def _entry(*, ok: bool):
        """Build a fake finished ConsoleLogEntry."""
        entry = mock.Mock()
        entry.ok = ok
        return entry

    def test_cli_launch_failure_notifies_unmarks_refreshes(self) -> None:
        _, app_class = import_app()
        instance, on_complete = self._run_and_capture_on_complete(
            app_class, "_start_cli_task_background"
        )
        on_complete(self._entry(ok=False))
        instance._unmark_launching.assert_called_once_with("proj1", "5")
        instance.notify.assert_called_once()
        assert instance.notify.call_args[1].get("severity") == "error"
        instance.refresh_tasks.assert_awaited()

    def test_cli_launch_success_unmarks_and_refreshes_quietly(self) -> None:
        _, app_class = import_app()
        instance, on_complete = self._run_and_capture_on_complete(
            app_class, "_start_cli_task_background"
        )
        instance.notify.reset_mock()
        on_complete(self._entry(ok=True))
        instance._unmark_launching.assert_called_once_with("proj1", "5")
        instance.notify.assert_not_called()
        instance.refresh_tasks.assert_awaited()

    def test_toad_launch_failure_notifies_error(self) -> None:
        _, app_class = import_app()
        instance, on_complete = self._run_and_capture_on_complete(
            app_class, "_start_toad_task_background"
        )
        instance.notify.reset_mock()
        on_complete(self._entry(ok=False))
        instance._unmark_launching.assert_called_once_with("proj1", "5")
        instance.notify.assert_called_once()
        assert instance.notify.call_args[1].get("severity") == "error"

    def test_toad_launch_success_notifies_running(self) -> None:
        _, app_class = import_app()
        instance, on_complete = self._run_and_capture_on_complete(
            app_class, "_start_toad_task_background"
        )
        instance.notify.reset_mock()
        on_complete(self._entry(ok=True))
        instance._unmark_launching.assert_called_once_with("proj1", "5")
        instance.notify.assert_called_once_with("Toad task 5 is running")


# ---------------------------------------------------------------------------
# New-task ``t`` binding in both panes (#1025)
# ---------------------------------------------------------------------------


class TestNewTaskBinding:
    """The New-task shortcut is ``t`` and reachable from either pane."""

    @staticmethod
    def _binding_map(bindings: object) -> dict[str, str]:
        """Map binding keys to their action strings for either tuple/Binding form."""
        return {
            (b[0] if isinstance(b, tuple) else b.key): (b[1] if isinstance(b, tuple) else b.action)
            for b in bindings
        }

    def test_task_list_new_binding_is_t(self) -> None:
        from tests.unit.tui.tui_test_helpers import import_widgets

        bindings = self._binding_map(import_widgets().TaskList.BINDINGS)
        # ``t`` replaced the old ``n`` so the shortcut matches the project pane.
        assert bindings.get("t") == "app.create_task_from_main"
        assert "n" not in bindings

    def test_project_list_exposes_new_task_binding(self) -> None:
        from tests.unit.tui.tui_test_helpers import import_widgets

        bindings = self._binding_map(import_widgets().ProjectList.BINDINGS)
        # New task from the project pane, while ``n`` stays the project wizard.
        assert bindings.get("t") == "app.create_task_from_main"
        assert bindings.get("n") == "app.new_project_wizard"

    def test_login_moved_to_i_freeing_l_for_vim(self) -> None:
        """Login is ``i`` (Log[i]n) so lowercase ``l`` is free for vim-right."""
        from tests.unit.tui.tui_test_helpers import import_widgets

        bindings = self._binding_map(import_widgets().TaskList.BINDINGS)
        assert bindings.get("i") == "app.login_from_main"
        assert "l" not in bindings, "l must stay free for vim navigation"

    def test_verdicts_moved_to_v(self) -> None:
        """Verdicts is ``v`` (first-letter) — clearer than the old ``i``."""
        from tests.unit.tui.tui_test_helpers import import_widgets

        bindings = self._binding_map(import_widgets().TaskList.BINDINGS)
        assert bindings.get("v") == "app.shield_interactive_from_main"


# ---------------------------------------------------------------------------
# TaskLaunchScreen.compose border title
# ---------------------------------------------------------------------------


class TestTaskLaunchScreenCompose:
    """Test that compose() sets the expected border title with the task name."""

    @staticmethod
    def _run_compose(screens_mod: types.ModuleType, screen: Any) -> Any | None:
        """Exhaust compose() and return the Vertical dialog with border_title.

        Patches the stub Vertical's ``__enter__`` to capture the dialog
        instance that ``compose()`` uses via ``with Vertical(...) as dialog:``.
        """
        Vertical = screens_mod.Vertical
        captured: list[Any] = []
        orig_enter = Vertical.__enter__

        def tracking_enter(self: Any) -> Any:
            captured.append(self)
            return orig_enter(self)

        Vertical.__enter__ = tracking_enter
        try:
            list(screen.compose())
        finally:
            Vertical.__enter__ = orig_enter
        return captured[0] if captured else None

    def test_compose_border_title_includes_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="3", task_name="fix-auth"
        )
        dialog = self._run_compose(screens, screen)
        assert dialog is not None
        assert dialog.border_title == "CLI Task 3 (fix-auth)"

    def test_compose_border_title_fallback_to_id(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="7")
        dialog = self._run_compose(screens, screen)
        assert dialog is not None
        assert dialog.border_title == "CLI Task 7 (7)"

    def test_compose_renders_select_disabled_while_loading(self) -> None:
        """While ``_installed is None`` the agent Select is rendered disabled."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", installed=None
        )
        widgets = list(screen.compose())
        selects = [w for w in widgets if isinstance(w, screens.Select)]
        assert selects, "compose() yielded no Select widget"
        sel = selects[0]
        assert sel._stub_kwargs.get("disabled") is True
        assert sel._stub_kwargs.get("prompt") == "Loading agents…"
        # In the loading state only bash is in choices.
        choices = sel._stub_args[0]
        assert choices == [("bash", "bash")]

    def test_compose_renders_select_enabled_when_loaded(self) -> None:
        """Once ``_installed`` is set, the dropdown renders enabled with providers."""
        from terok.lib.integrations.executor import AGENTS

        screens, _ = import_screens()
        target = next(iter(AGENTS))
        screen = screens.TaskLaunchScreen(
            container_name="c",
            project_name="p",
            task_id="1",
            installed=frozenset({target}),
        )
        widgets = list(screen.compose())
        selects = [w for w in widgets if isinstance(w, screens.Select)]
        sel = selects[0]
        assert sel._stub_kwargs.get("disabled") is False
        assert sel._stub_kwargs.get("prompt") == "Select an agent"
        choices = sel._stub_args[0]
        assert ("bash", "bash") in choices
        assert (AGENTS[target].label, target) in choices


# ---------------------------------------------------------------------------
# _SubmittablePromptArea — Enter submits (bubbles); modifiers insert newlines
# ---------------------------------------------------------------------------


class TestSubmittablePromptArea:
    """Verify Enter bubbles to submit while modifier+Enter inserts a newline.

    These tests use Textual's ``Pilot`` against a real ``App`` so the
    actual message-pump dispatch is exercised — the previous mock-event
    shape couldn't catch a regression where the override returned early
    but Textual still ran the default handler, because the assertion only
    checked that ``event.stop`` / ``event.prevent_default`` were *not*
    called.  That's exactly how the missing-``prevent_default()`` bug
    that surfaced as "Enter inserts a newline instead of submitting"
    got past CI.

    Imports the real ``terok.tui.screens`` (not via the stub-injecting
    ``import_screens`` helper used elsewhere in this file) because a stub
    Textual won't dispatch events through a real message-pump.
    """

    @pytest.mark.asyncio
    async def test_enter_does_not_insert_into_textarea(self) -> None:
        """Pressing Enter while the prompt has focus must not write ``\\n``.

        The override needs ``event.prevent_default()``; without it the
        widget's stock handler still runs and inserts a newline.
        """
        from textual.app import App
        from textual.widgets import TextArea

        from terok.tui.screens import _SubmittablePromptArea

        class _Host(App):
            def compose(self):
                yield _SubmittablePromptArea(id="probe")

        app = _Host()
        async with app.run_test() as pilot:
            area = app.query_one("#probe", TextArea)
            area.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert area.text == "", "Enter must not insert into the prompt"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["ctrl+enter", "shift+enter", "ctrl+j"])
    async def test_modifier_enter_inserts_a_single_newline(self, key: str) -> None:
        """Ctrl+Enter / Shift+Enter / Ctrl+J each insert exactly one newline.

        The widget owns this now (the host screen no longer touches it), so a
        focused prompt must end up with one ``\\n`` and no double-up.
        """
        from textual.app import App
        from textual.widgets import TextArea

        from terok.tui.screens import _SubmittablePromptArea

        class _Host(App):
            def compose(self):
                yield _SubmittablePromptArea(id="probe")

        app = _Host()
        async with app.run_test() as pilot:
            area = app.query_one("#probe", TextArea)
            area.focus()
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
        assert area.text == "\n", f"{key} must insert exactly one newline"

    @pytest.mark.asyncio
    async def test_modifier_enter_does_not_bubble_to_parent(self) -> None:
        """A newline modifier is consumed by the widget — the screen never sees it."""
        from textual.app import App
        from textual.widgets import TextArea

        from terok.tui.screens import _SubmittablePromptArea

        seen: list[str] = []

        class _Host(App):
            def compose(self):
                yield _SubmittablePromptArea(id="probe")

            def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
                seen.append(event.key)

        app = _Host()
        async with app.run_test() as pilot:
            area = app.query_one("#probe", TextArea)
            area.focus()
            await pilot.pause()
            await pilot.press("ctrl+j")
            await pilot.pause()
        assert "ctrl+j" not in seen, "newline modifier must not bubble to the screen"

    @pytest.mark.asyncio
    async def test_enter_bubbles_to_parent_screen_on_key(self) -> None:
        """The override must *not* call ``event.stop()`` — the host screen's
        ``on_key`` is what turns Enter into a form-submit."""
        from textual.app import App
        from textual.widgets import TextArea

        from terok.tui.screens import _SubmittablePromptArea

        seen: list[str] = []

        class _Host(App):
            def compose(self):
                yield _SubmittablePromptArea(id="probe")

            def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
                seen.append(event.key)

        app = _Host()
        async with app.run_test() as pilot:
            area = app.query_one("#probe", TextArea)
            area.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert "enter" in seen, "Enter must bubble to the parent for submission"

    @pytest.mark.asyncio
    async def test_other_keys_still_insert_normally(self) -> None:
        """Non-Enter keys fall through to TextArea's default — typing still works."""
        from textual.app import App
        from textual.widgets import TextArea

        from terok.tui.screens import _SubmittablePromptArea

        class _Host(App):
            def compose(self):
                yield _SubmittablePromptArea(id="probe")

        app = _Host()
        async with app.run_test() as pilot:
            area = app.query_one("#probe", TextArea)
            area.focus()
            await pilot.pause()
            await pilot.press("h", "i")
            await pilot.pause()
        assert area.text == "hi"


# ---------------------------------------------------------------------------
# _action_login uses _login_title
# ---------------------------------------------------------------------------


class TestActionLoginTitle:
    """Test that _action_login uses the unified _login_title format."""

    def test_action_login_passes_unified_title(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance.current_task = mock.Mock()
        instance.current_task.task_id = "5"
        instance.current_task.name = "fix-login-bug"
        instance.current_task.mode = "cli"
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._action_login.__globals__

        with mock.patch.dict(
            action_globals,
            {
                "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                "container_name": lambda *a: "proj1-cli-5",
            },
        ):
            run(app_class._action_login(instance))

        instance._launch_terminal_session.assert_awaited_once()
        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:5:fix-login-bug"
        assert call_kwargs["cname"] == "proj1-cli-5"

    def test_action_login_falls_back_to_task_id_when_unnamed(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance.current_task = mock.Mock()
        instance.current_task.task_id = "8"
        instance.current_task.name = ""
        instance.current_task.mode = "run"
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._action_login.__globals__

        with mock.patch.dict(
            action_globals,
            {
                "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                "container_name": lambda *a: "proj1-run-8",
            },
        ):
            run(app_class._action_login(instance))

        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:8:8"

    def test_action_login_no_task_notifies(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance.current_task = None
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        run(app_class._action_login(instance))

        instance.notify.assert_called_once_with("No task selected.")
        instance._launch_terminal_session.assert_not_awaited()

    def test_action_login_refused_when_web_served(self) -> None:
        """Under textual-serve there is no host terminal — CLI login is refused."""
        _, app_class = import_app()
        instance = app_class()
        instance.is_web = True
        instance.current_project_name = "proj1"
        instance.current_task = mock.Mock()
        instance.current_task.task_id = "5"
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        run(app_class._action_login(instance))

        instance._launch_terminal_session.assert_not_awaited()
        instance.notify.assert_called_once()
        assert instance.notify.call_args[1].get("severity") == "error"


# ---------------------------------------------------------------------------
# _login_title helper
# ---------------------------------------------------------------------------


class TestLoginTitle:
    """Tests for the _login_title helper that unifies terminal/tmux titles."""

    def _import_helper(self) -> Callable[[str, str, str], str]:
        """Import the _login_title helper from the freshly loaded module."""
        _, app_class = import_app()
        return app_class._start_cli_task_background.__globals__["_login_title"]

    def test_basic_format(self) -> None:
        login_title = self._import_helper()
        assert login_title("myproj", "3", "fix-auth-bug") == "myproj:3:fix-auth-bug"

    def test_name_equals_id_when_unnamed(self) -> None:
        login_title = self._import_helper()
        assert login_title("proj", "7", "7") == "proj:7:7"

    @pytest.mark.parametrize(
        ("pid", "tid", "name", "expected"),
        [
            ("a", "1", "x", "a:1:x"),
            ("long-project-name", "42", "refactor-db", "long-project-name:42:refactor-db"),
        ],
        ids=["short", "long"],
    )
    def test_parametrized(self, pid: str, tid: str, name: str, expected: str) -> None:
        login_title = self._import_helper()
        assert login_title(pid, tid, name) == expected


# ---------------------------------------------------------------------------
# TaskLaunchScreen — task_name propagation
# ---------------------------------------------------------------------------


class TestTaskLaunchScreenNamePropagation:
    """Tests for task_name flowing through TaskLaunchScreen."""

    def test_empty_name_falls_back_to_task_id(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="42", task_name=""
        )
        assert screen._task_name == "42"

    def test_none_name_falls_back_to_task_id(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="5", task_name=None
        )
        assert screen._task_name == "5"

    def test_explicit_name_preserved(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_name="p", task_id="1", task_name="fix-login"
        )
        assert screen._task_name == "fix-login"

    def test_do_login_result_includes_name(self) -> None:
        """The 6-tuple dismiss result includes task_name at position 2."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="ctr", project_name="proj", task_id="9", task_name="deploy-fix"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "vibe"
        mock_textarea = mock.Mock()
        mock_textarea.text = "refactor auth"

        screen.query_one = lambda sel, cls=None: (
            mock_select if "login-agent" in sel else mock_textarea
        )

        screen._do_login()
        result = screen.dismiss.call_args[0][0]
        assert len(result) == 6
        assert result == ("proj", "9", "deploy-fix", "ctr", "vibe", "refactor auth")

    def test_do_login_unnamed_task_uses_id(self) -> None:
        """When no task_name given, the result falls back to task_id."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_name="p", task_id="11")
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "bash"
        mock_textarea = mock.Mock()
        mock_textarea.text = ""

        screen.query_one = lambda sel, cls=None: (
            mock_select if "login-agent" in sel else mock_textarea
        )

        screen._do_login()
        result = screen.dismiss.call_args[0][0]
        # task_name should fall back to task_id "11"
        assert result[2] == "11"


# ---------------------------------------------------------------------------
# _start_cli_task_background passes name to TaskLaunchScreen
# ---------------------------------------------------------------------------


class TestStartCliTaskBackgroundPassesName:
    """Verify _start_cli_task_background forwards the task name to TaskLaunchScreen."""

    def test_task_name_forwarded_to_launch_screen(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance._last_selected_tasks = {}
        instance._save_selection_state = mock.Mock()
        instance.notify = mock.Mock()
        instance.dispatch_console_action = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        instance.refresh_tasks = mock.AsyncMock()
        instance._mark_launching = mock.Mock()
        instance.run_worker = mock.Mock()

        fake_project = mock.Mock()
        fake_project.default_shell = "claude"
        action_globals = app_class._start_cli_task_background.__globals__

        with mock.patch.dict(
            action_globals,
            {
                "task_new": mock.Mock(return_value="7"),
                "load_project": mock.Mock(return_value=fake_project),
                "container_name": lambda *a: "terok-proj1-cli-7",
            },
        ):
            run(app_class._start_cli_task_background(instance, "deploy-hotfix"))

        # Verify push_screen was called with a TaskLaunchScreen
        instance.push_screen.assert_awaited_once()
        launch_screen = instance.push_screen.call_args[0][0]
        assert launch_screen._task_name == "deploy-hotfix"
        assert launch_screen._task_id == "7"
        assert launch_screen._project_name == "proj1"
        assert launch_screen._default_shell == "claude"
        # Launch screen is pushed in the "loading" state so the prompt
        # TextArea is typeable immediately — the agent dropdown fills in
        # when the worker resolves _fill_installed_agents.
        assert launch_screen._installed is None
        # The worker for the agents lookup was kicked off.
        instance.run_worker.assert_called_once()


# ---------------------------------------------------------------------------
# _start_cli_task_background — load_project failure path
# ---------------------------------------------------------------------------


class TestStartCliTaskBackgroundLoadFailure:
    """When ``load_project`` fails, the launch screen still surfaces immediately.

    The agent dropdown is finalised with an empty (no-filter) installed set
    in-line — no worker — and a debug line records the failure.
    """

    def test_launch_screen_finalised_when_load_project_fails(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance._last_selected_tasks = {}
        instance._save_selection_state = mock.Mock()
        instance.notify = mock.Mock()
        instance.dispatch_console_action = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        instance.refresh_tasks = mock.AsyncMock()
        instance._mark_launching = mock.Mock()
        instance.run_worker = mock.Mock()
        instance._log_debug = mock.Mock()

        action_globals = app_class._start_cli_task_background.__globals__
        with mock.patch.dict(
            action_globals,
            {
                "task_new": mock.Mock(return_value="9"),
                "load_project": mock.Mock(side_effect=RuntimeError("boom")),
                "container_name": lambda *a: "terok-proj1-cli-9",
            },
        ):
            run(app_class._start_cli_task_background(instance, "fix-thing"))

        # Failure was logged via _log_debug (the project lookup failure path).
        instance._log_debug.assert_called()
        # No worker — the helper short-circuits to set_installed(frozenset()).
        instance.run_worker.assert_not_called()
        # Launch screen was pushed with installed already resolved (empty set).
        instance.push_screen.assert_awaited_once()
        launch_screen = instance.push_screen.call_args[0][0]
        assert launch_screen._installed == frozenset()
        assert launch_screen._default_shell == "bash"


# ---------------------------------------------------------------------------
# _fill_installed_agents — off-thread worker that feeds set_installed
# ---------------------------------------------------------------------------


class TestFillInstalledAgents:
    """The worker that resolves installed agents and pushes them into the screen."""

    def test_resolves_and_feeds_set_installed(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance._log_debug = mock.Mock()

        fake_project = mock.Mock()
        launch_screen = mock.Mock()

        # The helper does ``from ..lib.api import installed_agents_for_project``
        # at call time, so patch the symbol on the source module.
        with mock.patch(
            "terok.lib.api.installed_agents_for_project",
            return_value=frozenset({"claude"}),
        ):
            run(app_class._fill_installed_agents(instance, launch_screen, fake_project))

        launch_screen.set_installed.assert_called_once_with(frozenset({"claude"}))
        instance._log_debug.assert_not_called()

    def test_failure_falls_back_to_empty_and_logs(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance._log_debug = mock.Mock()

        fake_project = mock.Mock()
        fake_project.name = "proj1"
        launch_screen = mock.Mock()

        with mock.patch(
            "terok.lib.api.installed_agents_for_project",
            side_effect=RuntimeError("podman exploded"),
        ):
            run(app_class._fill_installed_agents(instance, launch_screen, fake_project))

        launch_screen.set_installed.assert_called_once_with(frozenset())
        instance._log_debug.assert_called_once()
        msg = instance._log_debug.call_args[0][0]
        assert "podman exploded" in msg
        assert "proj1" in msg


# ---------------------------------------------------------------------------
# _on_launch_screen_result terminal title
# ---------------------------------------------------------------------------


class TestOnLaunchScreenResultTitle:
    """Verify _on_launch_screen_result uses the unified login title."""

    def test_bash_login_title_includes_task_name(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance.refresh_tasks = mock.AsyncMock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._on_launch_screen_result.__globals__

        with mock.patch.dict(
            action_globals,
            {"get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c", "bash"])},
        ):
            result = ("proj1", "3", "fix-auth", "proj1-cli-3", "bash", None)
            run(app_class._on_launch_screen_result(instance, result))

        instance._launch_terminal_session.assert_awaited_once()
        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:3:fix-auth"
        assert call_kwargs["cname"] == "proj1-cli-3"

    def test_agent_login_title_includes_task_name(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance.refresh_tasks = mock.AsyncMock()
        instance._launch_terminal_session = mock.AsyncMock()

        fake_provider = mock.Mock()
        fake_provider.binary = "claude"

        action_globals = app_class._on_launch_screen_result.__globals__

        with (
            mock.patch.dict(
                action_globals,
                {
                    "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                    "_save_initial_prompt": mock.Mock(),
                },
            ),
            mock.patch.dict(
                "terok_executor.provider.providers.AGENTS",
                {"claude": fake_provider},
                clear=True,
            ),
        ):
            result = ("proj1", "5", "my-task", "proj1-cli-5", "claude", "fix it")
            run(app_class._on_launch_screen_result(instance, result))

        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:5:my-task"

    def test_none_result_refreshes_tasks(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.refresh_tasks = mock.AsyncMock()
        instance._launch_terminal_session = mock.AsyncMock()

        run(app_class._on_launch_screen_result(instance, None))

        instance.refresh_tasks.assert_awaited_once()
        instance._launch_terminal_session.assert_not_awaited()

    def test_unknown_agent_notifies(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_name = "proj1"
        instance.refresh_tasks = mock.AsyncMock()
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._on_launch_screen_result.__globals__

        with (
            mock.patch.dict(
                action_globals,
                {
                    "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                    "_save_initial_prompt": mock.Mock(),
                },
            ),
            mock.patch.dict(
                "terok_executor.provider.providers.AGENTS",
                {},
                clear=True,
            ),
        ):
            result = ("proj1", "5", "my-task", "proj1-cli-5", "nonexistent", "hi")
            run(app_class._on_launch_screen_result(instance, result))

        instance.notify.assert_called_once_with("Unknown agent: nonexistent")
        instance._launch_terminal_session.assert_not_awaited()
