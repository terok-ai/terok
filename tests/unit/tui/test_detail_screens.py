# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI detail screens (Phase 2) and rendering helpers."""

import asyncio
import contextlib
import inspect
import sys
from collections.abc import Callable
from unittest import mock

import pytest
from rich.text import Text

from tests.testfs import MOCK_BASE, MOCK_CONFIG_ROOT
from tests.testnet import GATE_PORT, TEST_EGRESS_URL, TEST_UPSTREAM_URL
from tests.unit.tui.tui_test_helpers import (
    import_app,
    import_screens,
    import_widgets,
    make_key_event,
)

MOCK_WORKSPACE = str(MOCK_BASE / "ws")
TEST_PROJECT_ID = "test-proj"
TEST_PROJECT_ROOT = MOCK_CONFIG_ROOT / "projects" / TEST_PROJECT_ID


def make_project(**overrides: object) -> mock.Mock:
    """Return a project mock with sensible defaults for TUI rendering tests."""
    project = mock.Mock()
    project.id = TEST_PROJECT_ID
    project.upstream_url = TEST_UPSTREAM_URL
    project.security_class = "online"
    project.agents = ["codex"]
    project.agent_config = {}
    project.root = TEST_PROJECT_ROOT
    for key, value in overrides.items():
        setattr(project, key, value)
    return project


def make_task(widgets: object, **overrides: object) -> object:
    """Build a TaskMeta with defaults tuned for these tests."""
    defaults: dict[str, object] = {
        "task_id": "1",
        "mode": "cli",
        "workspace": MOCK_WORKSPACE,
        "web_port": None,
        "container_state": "running",
    }
    merged = defaults | overrides
    merged.setdefault("initialized", merged["mode"] is not None)
    return widgets.TaskMeta(**merged)


def make_task_screen(*, has_tasks: bool, mode: str | None = None) -> object:
    """Build a TaskDetailsScreen with a mocked dismiss method."""
    screens, widgets = import_screens()
    task = None if mode is None else make_task(widgets, task_id="t1", mode=mode)
    screen = screens.TaskDetailsScreen(task=task, has_tasks=has_tasks, project_id="p")
    screen.dismiss = mock.Mock()
    return screen


def render_task_details_text(**overrides: object) -> str:
    """Render task details and return plain text for substring assertions."""
    widgets = import_widgets()
    task = make_task(widgets, **overrides)
    return str(widgets.render_task_details(task, project_id="proj1"))


def format_task_label(**overrides: object) -> str:
    """Format a task label using the shared TaskMeta defaults."""
    widgets = import_widgets()
    return widgets.TaskList()._format_task_label(make_task(widgets, **overrides))


def run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


def assert_rendered_needles(text: str, present: list[str], absent: list[str]) -> None:
    """Assert that required needles are present and forbidden ones absent."""
    for needle in present:
        assert needle in text
    for needle in absent:
        assert needle not in text


async def fake_push_screen(
    _screen: object,
    callback: Callable[[str], object],
) -> None:
    """Simulate a modal that immediately returns a generated task name."""
    result = callback("test-name")
    if inspect.isawaitable(result):
        await result


def make_creation_app(app_class: type) -> object:
    """Build a TUI app instance prepared for task-creation workflows."""
    instance = app_class()
    instance.current_project_id = "proj1"
    instance._last_selected_tasks = {}
    instance.notify = mock.Mock()
    instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
    instance._save_selection_state = mock.Mock()
    instance.refresh_tasks = mock.AsyncMock()
    instance.push_screen = fake_push_screen
    instance._mark_launching = mock.Mock()
    instance.run_worker = mock.Mock()
    return instance


def make_gate_server_status(
    *, mode: str = "systemd", running: bool = True, port: int = GATE_PORT
) -> mock.Mock:
    """Build a gate-server status mock with common defaults."""
    status = mock.Mock()
    status.mode = mode
    status.running = running
    status.port = port
    return status


def _task_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.TASK_ACTION_HANDLERS.items())


def _auth_providers() -> list[str]:
    from terok_executor import AUTH_PROVIDERS

    return list(AUTH_PROVIDERS)


def _project_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.PROJECT_ACTION_HANDLERS.items())


def _gate_server_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.GATE_SERVER_ACTION_HANDLERS.items())


class TestRenderHelpers:
    """Tests for the extracted render_* helper functions."""

    def test_render_project_details_returns_text(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {
            "ssh": True,
            "dockerfiles": True,
            "images": True,
            "gate": True,
        }

        result = widgets.render_project_details(project, state, task_count=5)

        assert isinstance(result, Text)
        text_str = str(result)
        assert TEST_PROJECT_ID in text_str

    def test_render_project_details_shows_config_path(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}

        result = widgets.render_project_details(project, state, task_count=5)
        text_str = str(result)
        assert f"Config: {TEST_PROJECT_ROOT}" in text_str

    def test_render_project_details_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_details(None, None)

        assert isinstance(result, Text)
        assert "No project" in str(result)

    def test_render_task_details_returns_text(self) -> None:
        widgets = import_widgets()

        task = make_task(widgets, task_id="42", backend="codex")

        result = widgets.render_task_details(task, project_id="proj1")

        assert isinstance(result, Text)
        text_str = str(result)
        assert "42" in text_str

    def test_render_task_details_none_shows_empty_message(self) -> None:
        widgets = import_widgets()

        result = widgets.render_task_details(None, empty_message="Nothing here")

        assert isinstance(result, Text)
        assert "Nothing here" in str(result)

    def test_render_project_loading(self) -> None:
        widgets = import_widgets()
        project = make_project(id="myproj", upstream_url=TEST_EGRESS_URL)

        result = widgets.render_project_loading(project, task_count=3)

        assert isinstance(result, Text)
        text_str = str(result)
        assert "myproj" in text_str

    def test_render_project_loading_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_loading(None)

        assert isinstance(result, Text)
        assert "No project" in str(result)

    def test_render_task_details_autopilot_mode(self) -> None:
        widgets = import_widgets()
        task = make_task(widgets, task_id="5", mode="run")
        result = widgets.render_task_details(task, project_id="proj1")
        assert isinstance(result, Text)
        text_str = str(result)
        assert "Autopilot" in text_str
        assert "terok task logs" in text_str

    def test_render_task_details_autopilot_with_exit_code(self) -> None:
        widgets = import_widgets()
        task = make_task(widgets, task_id="5", mode="run", exit_code=0)
        result = widgets.render_task_details(task, project_id="proj1")
        text_str = str(result)
        assert "Exit code: 0" in text_str

    def test_web_url_emits_osc8_link_outside_web_mode(self) -> None:
        """In a real terminal the URL style carries an OSC 8 link for cross-line wrap."""
        import io

        from rich.console import Console

        widgets = import_widgets()
        task = make_task(widgets, task_id="42", mode="toad", web_port=8123, web_token="t0k")
        result = widgets.render_task_details(task, project_id="proj1", is_web=False)
        # Render to a buffer with ``force_terminal`` so styles serialise
        # to ANSI; the plain ``str(result)`` projection drops styling.
        buf = io.StringIO()
        Console(file=buf, width=80, force_terminal=True, color_system="standard").print(result)
        ansi = buf.getvalue()
        assert "\x1b]8;" in ansi  # OSC 8 sequence present
        assert "id=" in ansi  # shared id for cross-line stitching

    def test_web_url_skips_osc8_when_web_mode(self) -> None:
        """In web mode (xterm.js) we omit OSC 8 to avoid the dangerous-link dialog."""
        import io

        from rich.console import Console

        widgets = import_widgets()
        task = make_task(widgets, task_id="42", mode="toad", web_port=8123, web_token="t0k")
        result = widgets.render_task_details(task, project_id="proj1", is_web=True)
        buf = io.StringIO()
        Console(file=buf, width=80, force_terminal=True, color_system="standard").print(result)
        ansi = buf.getvalue()
        assert "\x1b]8;" not in ansi  # no OSC 8 — Textual @click handles the click
        # The URL itself is still in the rendered text so users can copy it.
        assert "8123" in ansi

    @pytest.mark.parametrize(
        ("overrides", "present", "absent"),
        [
            pytest.param(
                {
                    "task_id": "10",
                    "mode": "run",
                    "work_status": "coding",
                    "work_message": "Implementing JWT validation",
                },
                ["Work:", "coding", "Implementing JWT validation"],
                [],
                id="work-status-with-message",
            ),
            pytest.param(
                {"task_id": "11", "mode": "run", "work_status": "testing"},
                ["Work:", "testing"],
                [],
                id="work-status-no-message",
            ),
            pytest.param(
                {"task_id": "12", "mode": "cli"},
                [],
                ["Work:"],
                id="no-work-status",
            ),
        ],
    )
    def test_render_task_details_work_status_variants(
        self, overrides: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(render_task_details_text(**overrides), present, absent)

    @pytest.mark.parametrize(
        ("overrides", "present", "absent"),
        [
            pytest.param(
                {"task_id": "20", "mode": "run", "unrestricted": True},
                ["Perms:     unrestricted"],
                [],
                id="unrestricted",
            ),
            pytest.param(
                {"task_id": "21", "mode": "run", "unrestricted": False},
                ["Perms:     restricted"],
                ["Perms:     unrestricted"],
                id="restricted",
            ),
        ],
    )
    def test_render_task_details_permission_variants(
        self, overrides: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(render_task_details_text(**overrides), present, absent)

    @pytest.mark.parametrize(
        ("shield_state", "present", "absent"),
        [
            pytest.param(
                "DISABLED",
                ["Shield:", "disabled", "shield-security"],
                [],
                id="disabled",
            ),
            pytest.param(
                "OFFLINE",
                ["offline", "shield-security"],
                [],
                id="offline-running",
            ),
            pytest.param(
                "UP",
                ["up"],
                ["shield-security"],
                id="up",
            ),
        ],
    )
    def test_render_task_details_shield_variants(
        self, shield_state: str, present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(
            render_task_details_text(task_id="99", shield_state=shield_state),
            present,
            absent,
        )

    def test_render_shield_offline_stopped_hooks_ok_shows_ready(self) -> None:
        """Stopped containers with healthy hooks show 'ready', no warning."""
        widgets = import_widgets()
        task = make_task(widgets, task_id="99", shield_state="OFFLINE", container_state="exited")
        text = str(widgets.render_task_details(task, project_id="proj1", shield_hooks_ok=True))
        assert "ready" in text
        assert "offline" not in text
        assert "shield-security" not in text

    def test_render_shield_offline_stopped_hooks_broken_shows_warning(self) -> None:
        """Stopped containers with broken hooks still show offline warning."""
        widgets = import_widgets()
        task = make_task(widgets, task_id="99", shield_state="OFFLINE", container_state="exited")
        text = str(widgets.render_task_details(task, project_id="proj1", shield_hooks_ok=False))
        assert "offline" in text
        assert "shield-security" in text
        assert "ready" not in text

    @pytest.mark.parametrize(
        ("overrides", "present", "absent"),
        [
            pytest.param(
                {"task_id": "13", "mode": "run", "work_status": "debugging"},
                ["work=debugging"],
                [],
                id="with-work-status",
            ),
            pytest.param(
                {"task_id": "14", "mode": "cli"},
                [],
                ["work="],
                id="without-work-status",
            ),
            pytest.param(
                {"task_id": "3", "mode": "run"},
                ["🚀"],
                [],
                id="autopilot",
            ),
        ],
    )
    def test_format_task_label_variants(
        self, overrides: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(format_task_label(**overrides), present, absent)

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            pytest.param({"task_id": "1", "mode": "run", "exit_code": 1}, 1, id="explicit-exit"),
            pytest.param({"task_id": "1", "mode": "cli"}, None, id="default-none"),
        ],
    )
    def test_task_meta_exit_code_variants(
        self, overrides: dict[str, object], expected: int | None
    ) -> None:
        widgets = import_widgets()
        task = make_task(widgets, **overrides)
        assert task.exit_code == expected


class TestTextualMethodNameCollisions:
    """Guard against shadowing Textual Widget rendering hooks.

    Defining ``_render`` (or ``_render_content``) on a Widget subclass
    breaks the renderer with ``'NoneType' has no attribute
    'render_strips'`` because Textual calls ``self._render()`` to obtain
    a Visual during repaint.  Custom redraw helpers must use a different
    name (we use ``_redraw_content``).
    """

    RESERVED_BY_TEXTUAL = ("_render", "_render_content", "_render_widget")

    def test_task_details_does_not_shadow_textual_render(self) -> None:
        widgets = import_widgets()
        defined = vars(widgets.TaskDetails)
        for name in self.RESERVED_BY_TEXTUAL:
            assert name not in defined, (
                f"TaskDetails.{name} would shadow Textual's Widget.{name} "
                "and crash the renderer; rename to e.g. _redraw_content."
            )


class TestScreenConstruction:
    """Tests that screen classes can be instantiated with correct arguments."""

    def test_project_details_screen_construction(self) -> None:
        screens, _ = import_screens()
        project = make_project(id="proj1")
        staleness = mock.Mock()

        screen = screens.ProjectDetailsScreen(
            project=project,
            state={"ssh": True},
            task_count=5,
            staleness=staleness,
        )
        assert screen._project == project
        assert screen._state == {"ssh": True}
        assert screen._task_count == 5
        assert screen._staleness == staleness

    def test_project_details_binds_uppercase_a_to_set_agents(self) -> None:
        """Uppercase ``A`` must map to ``set_agents`` so users can rebind freely."""
        screens, _ = import_screens()
        bindings = {
            (b._stub_args[0], b._stub_args[1]) for b in screens.ProjectDetailsScreen.BINDINGS
        }
        assert ("A", "set_agents") in bindings

    def test_project_details_action_set_agents_opens_modal(self) -> None:
        """``action_set_agents`` calls ``_open_agents_modal`` — keeps the wiring honest."""
        screens, _ = import_screens()
        project = make_project(id="proj1")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=0)
        screen._open_agents_modal = mock.Mock()
        screen.action_set_agents()
        screen._open_agents_modal.assert_called_once_with()

    def test_project_details_option_list_routes_set_agents(self) -> None:
        """Selecting the OptionList entry pushes the modal instead of dismissing."""
        screens, _ = import_screens()
        project = make_project(id="proj1")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=0)
        screen._open_agents_modal = mock.Mock()
        screen.dismiss = mock.Mock()
        event = mock.Mock()
        event.option_id = "set_agents"
        screen.on_option_list_option_selected(event)
        screen._open_agents_modal.assert_called_once_with()
        screen.dismiss.assert_not_called()

    def test_project_details_agents_modal_writes_selection(self) -> None:
        """A non-None selection from the modal lands in ``set_project_image_agents``."""
        screens, _ = import_screens()
        project = make_project(id="proj1", agents="all")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=0)
        screen.notify = mock.Mock()
        with mock.patch("terok.lib.api.set_project_image_agents") as write_mock:
            write_mock.return_value = "/tmp/terok-testing/proj1/project.yml"
            screen._on_agents_modal_result("claude,vibe")
        write_mock.assert_called_once_with("proj1", "claude,vibe")
        screen.notify.assert_called_once()
        call = screen.notify.call_args
        assert "claude,vibe" in call.args[0]

    def test_project_details_agents_modal_cancel_is_noop(self) -> None:
        """``None`` from the modal must NOT touch project.yml."""
        screens, _ = import_screens()
        project = make_project(id="proj1", agents="all")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=0)
        screen.notify = mock.Mock()
        with mock.patch("terok.lib.api.set_project_image_agents") as write_mock:
            screen._on_agents_modal_result(None)
        write_mock.assert_not_called()
        screen.notify.assert_not_called()

    def test_task_details_screen_construction(self) -> None:
        screens, widgets = import_screens()
        task = make_task(widgets, task_id="7", backend="codex")

        screen = screens.TaskDetailsScreen(
            task=task,
            has_tasks=True,
            project_id="proj1",
            image_old=False,
        )
        assert screen._task_meta == task
        assert screen._has_tasks
        assert screen._project_id == "proj1"
        assert not screen._image_old

    @pytest.mark.parametrize("screen_name", ["AuthActionsScreen", "AutopilotPromptScreen"])
    def test_simple_screen_construction(self, screen_name: str) -> None:
        screens, _ = import_screens()
        assert getattr(screens, screen_name)() is not None

    def test_agent_selection_screen_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen()
        assert screen is not None
        assert screen._default_agent == "claude"
        assert screen._subagents == []

    def test_agent_selection_screen_custom_default(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        assert screen._default_agent == "codex"

    def test_agent_selection_screen_with_subagents(self) -> None:
        screens, _ = import_screens()
        subagents = [
            {"name": "reviewer", "description": "Code reviewer", "default": True},
            {"name": "debugger", "description": "Debugger", "default": False},
        ]
        screen = screens.AgentSelectionScreen(subagents=subagents)
        assert screen is not None
        assert len(screen._subagents) == 2

    def test_agent_selection_screen_no_subagents(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(subagents=None)
        assert screen is not None
        assert screen._subagents == []

    def test_agent_selection_screen_invalid_default_falls_back(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="nonexistent")
        # Should fall back to first registered provider, not keep invalid name
        assert screen._default_agent != "nonexistent"
        assert screen._selected_agent == screen._default_agent

    def test_agent_selection_screen_cancel_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen()
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)

    def test_agent_selection_screen_submit_returns_tuple(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        screen.dismiss = mock.Mock()
        # Simulate submit without subagents — should return (agent, None)
        screen._submit()
        screen.dismiss.assert_called_once()
        result = screen.dismiss.call_args[0][0]
        assert isinstance(result, tuple)
        assert result[0] == "codex"
        assert result[1] is None

    def test_agent_selection_screen_number_key_updates_selection(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="claude")
        # Stub query_one to return a mock OptionList
        mock_option_list = mock.Mock()
        screen.query_one = mock.Mock(return_value=mock_option_list)
        # Press "1" — selects the first agent (alphabetically), which is not "claude"
        event = make_key_event("1")
        event.character = "1"
        screen.on_key(event)
        assert screen._selected_agent != "claude"
        event.stop.assert_called_once()


class TestTaskScreenKeyBinding:
    """Tests for TaskDetailsScreen.on_key case-sensitive dispatch."""

    @pytest.mark.parametrize(
        ("key", "has_tasks", "expected", "mode", "should_stop"),
        [
            pytest.param("c", False, "task_start_cli", None, True, id="lower-c"),
            pytest.param("w", False, "task_start_toad", None, True, id="lower-w"),
            pytest.param("A", False, "task_start_autopilot", None, True, id="shift-a"),
            pytest.param("H", True, "diff_head", None, None, id="shift-h"),
            pytest.param("P", True, "diff_prev", None, None, id="shift-p"),
            pytest.param("X", True, "delete", None, None, id="shift-x"),
            pytest.param("r", True, "restart", None, None, id="lower-r"),
            pytest.param("t", True, "stop", None, None, id="lower-t"),
            pytest.param("d", True, "shield_down", None, None, id="lower-d"),
            pytest.param("D", True, "shield_disengaged", None, None, id="shift-d"),
            pytest.param("s", True, "shield_up", None, None, id="lower-s"),
            pytest.param("escape", False, None, None, None, id="escape"),
            pytest.param("q", False, None, None, None, id="q"),
            pytest.param("f", True, "follow_logs", "run", None, id="follow-autopilot"),
            pytest.param("f", True, "follow_logs", "cli", None, id="follow-cli"),
        ],
    )
    def test_key_dispatch(
        self,
        key: str,
        has_tasks: bool,
        expected: str | None,
        mode: str | None,
        should_stop: bool | None,
    ) -> None:
        screen = make_task_screen(has_tasks=has_tasks, mode=mode)
        event = make_key_event(key)
        screen.on_key(event)
        screen.dismiss.assert_called_once_with(expected)
        if should_stop is True:
            event.stop.assert_called_once()
        elif should_stop is False:
            event.stop.assert_not_called()

    @pytest.mark.parametrize("key", ["H", "d", "D", "s", "f"])
    def test_task_only_keys_are_blocked_without_tasks(self, key: str) -> None:
        screen = make_task_screen(has_tasks=False)
        event = make_key_event(key)
        screen.on_key(event)
        screen.dismiss.assert_not_called()

    def test_unmapped_key_does_nothing(self) -> None:
        screen = make_task_screen(has_tasks=True)
        event = make_key_event("x")
        screen.on_key(event)
        screen.dismiss.assert_not_called()
        event.stop.assert_not_called()


class TestAuthScreenOptions:
    """Tests that AuthActionsScreen includes the import option."""

    def test_auth_screen_has_import_opencode_option(self) -> None:
        """Verify AuthActionsScreen includes import_opencode_config option."""
        screens, _ = import_screens()

        screen = screens.AuthActionsScreen()
        screen.dismiss = mock.Mock()

        # Simulate selecting the import option via on_option_list_option_selected
        event = mock.Mock()
        event.option_id = "import_opencode_config"
        screen.on_option_list_option_selected(event)
        screen.dismiss.assert_called_once_with("import_opencode_config")

    def test_auth_screen_number_key_triggers_import(self) -> None:
        """Verify the number key after last provider selects import option.

        The shortcut only exists when the provider count is below 9
        (single-digit keys 1-9).  When all 9 slots are occupied, the
        import option has no number shortcut and pressing the next
        number is a no-op.
        """
        from terok_executor import AUTH_PROVIDERS

        screens, _ = import_screens()
        screen = screens.AuthActionsScreen()
        screen.dismiss = mock.Mock()

        import_num = len(AUTH_PROVIDERS) + 1
        event = make_key_event(str(import_num))
        event.character = str(import_num)
        screen.on_key(event)
        if import_num <= 9:
            screen.dismiss.assert_called_once_with("import_opencode_config")
        else:
            screen.dismiss.assert_not_called()

    def test_opencode_config_screen_construction(self) -> None:
        """Verify OpenCodeConfigScreen can be instantiated."""
        screens, _ = import_screens()
        screen = screens.OpenCodeConfigScreen()
        assert screen is not None

    def test_opencode_config_screen_cancel(self) -> None:
        """Verify cancel action dismisses with None."""
        screens, _ = import_screens()
        screen = screens.OpenCodeConfigScreen()
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)


class TestActionDispatch:
    """Tests for action dispatch routing in the app."""

    def test_project_action_dispatch_project_init(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)

        run(app_class._handle_project_action(instance, "project_init"))

        instance._action_project_init.assert_called_once()

    @pytest.mark.parametrize("provider", _auth_providers())
    def test_project_action_dispatch_auth_providers(self, provider: str) -> None:
        """Auth dispatch extracts the provider name from the action string."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_project_action(instance, f"auth_{provider}"))
        instance._action_auth.assert_called_once_with(provider)

    def test_project_action_dispatch_import_opencode(self) -> None:
        """Import opencode config action routes to the handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_project_action(instance, "import_opencode_config"))
        instance._action_import_opencode_config.assert_called_once()

    @pytest.mark.parametrize(("action", "handler"), _task_action_cases())
    def test_task_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every entry in TASK_ACTION_HANDLERS routes to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_task_action(instance, action))
        getattr(instance, handler).assert_called_once()

    @pytest.mark.parametrize(("action", "handler"), _project_action_cases())
    def test_project_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every entry in PROJECT_ACTION_HANDLERS routes to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_project_action(instance, action))
        getattr(instance, handler).assert_called_once()

    def test_action_run_cli_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_run_cli_from_main(instance))
        instance._action_task_start_cli.assert_called_once()

    def test_action_delete_task_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_delete_task_from_main(instance))
        instance.action_delete_task.assert_called_once()

    def test_action_run_autopilot_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_run_autopilot_from_main(instance))
        instance._action_task_start_autopilot.assert_called_once()

    def test_action_follow_logs_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_follow_logs_from_main(instance))
        instance._action_follow_logs.assert_called_once()


class TestSSHKeyRegistration:
    """TUI SSH init / project-init dispatch to the worker_actions child entrypoints."""

    def _get_mixin(self):
        """Import ProjectActionsMixin directly — avoids import_app() Textual stubs."""
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_action_init_ssh_dispatches_init_ssh(self) -> None:
        """action_init_ssh dispatches the init_ssh worker action for the selection."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "proj"
        run(mixin.action_init_ssh(instance))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:init_ssh",
            "proj",
            title="Initializing SSH key for proj",
        )

    def test_action_project_init_opens_init_progress_screen(self) -> None:
        """_action_project_init reuses the wizard's InitProgressScreen.

        The full setup keeps the interactive deploy-key registration
        pause — running it as a pause-less child process would let
        gate-sync start before the key is registered.  The screen is
        opened with ``rendered_yaml=None`` so the existing project.yml
        is left untouched (no overwrite prompt, nothing rewritten).
        """
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "proj"
        instance.push_screen = mock.AsyncMock()
        run(mixin._action_project_init(instance))
        instance.push_screen.assert_awaited_once()
        screen = instance.push_screen.call_args[0][0]
        assert type(screen).__name__ == "InitProgressScreen"
        assert screen._project_id == "proj"
        assert screen._rendered_yaml is None


class TestEditInstructionsRouting:
    """``_edit_instructions_file`` chooses ``$EDITOR`` or the integrated editor."""

    def _get_mixin(self):
        """Import ProjectActionsMixin directly — avoids import_app() Textual stubs."""
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def _instance(self, mixin: object, *, is_web: bool) -> mock.Mock:
        """Build a mixin mock with the collaborators the editor routing touches."""
        instance = mock.Mock(spec=mixin)
        instance.is_web = is_web
        instance.push_screen = mock.AsyncMock()
        instance._edit_in_external_editor = mock.AsyncMock()
        instance._refresh_project_state = mock.Mock()
        instance.notify = mock.Mock()
        return instance

    def test_external_editor_used_when_set_and_preferred(self, tmp_path: object) -> None:
        """Local TUI + ``$EDITOR`` set + preference on → suspend into ``$EDITOR``."""
        mixin = self._get_mixin()
        instance = self._instance(mixin, is_web=False)
        instr_path = tmp_path / "instructions.md"
        with (
            mock.patch.dict("terok.tui.project_actions.os.environ", {"EDITOR": "vim"}),
            mock.patch(
                "terok.lib.api.get_config",
                return_value=mock.Mock(tui_external_editor=True),
            ),
        ):
            run(mixin._edit_instructions_file(instance, instr_path, title="T", done_msg="done"))
        instance._edit_in_external_editor.assert_awaited_once_with(
            instr_path, "vim", done_msg="done"
        )
        instance.push_screen.assert_not_awaited()

    def test_web_tui_forces_integrated_editor(self, tmp_path: object) -> None:
        """Web TUI ignores ``$EDITOR`` — there is no terminal to suspend to."""
        mixin = self._get_mixin()
        instance = self._instance(mixin, is_web=True)
        instr_path = tmp_path / "instructions.md"
        instr_path.write_text("body", encoding="utf-8")
        with (
            mock.patch.dict("terok.tui.project_actions.os.environ", {"EDITOR": "vim"}),
            mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls,
        ):
            run(mixin._edit_instructions_file(instance, instr_path, title="T", done_msg="done"))
        instance._edit_in_external_editor.assert_not_awaited()
        instance.push_screen.assert_awaited_once()
        screen_cls.assert_called_once_with("body", title="T")

    def test_preference_off_uses_integrated_editor(self, tmp_path: object) -> None:
        """``external_editor: false`` keeps the integrated editor even with ``$EDITOR`` set."""
        mixin = self._get_mixin()
        instance = self._instance(mixin, is_web=False)
        instr_path = tmp_path / "instructions.md"
        with (
            mock.patch.dict("terok.tui.project_actions.os.environ", {"EDITOR": "vim"}),
            mock.patch(
                "terok.lib.api.get_config",
                return_value=mock.Mock(tui_external_editor=False),
            ),
            mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls,
        ):
            run(mixin._edit_instructions_file(instance, instr_path, title="T", done_msg="done"))
        instance._edit_in_external_editor.assert_not_awaited()
        screen_cls.assert_called_once_with("", title="T")

    def test_no_editor_env_uses_integrated_editor(self, tmp_path: object) -> None:
        """No ``$EDITOR`` → integrated editor, without even consulting the preference."""
        mixin = self._get_mixin()
        instance = self._instance(mixin, is_web=False)
        instr_path = tmp_path / "instructions.md"
        with (
            mock.patch.dict("terok.tui.project_actions.os.environ", {}, clear=True),
            mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls,
        ):
            run(mixin._edit_instructions_file(instance, instr_path, title="T", done_msg="done"))
        instance._edit_in_external_editor.assert_not_awaited()
        screen_cls.assert_called_once_with("", title="T")

    def test_integrated_editor_save_callback_writes_and_refreshes(self, tmp_path: object) -> None:
        """The integrated editor's Save callback writes the file and refreshes state."""
        mixin = self._get_mixin()
        instance = self._instance(mixin, is_web=True)
        instr_path = tmp_path / "nested" / "instructions.md"
        with mock.patch("terok.tui.text_screens.TextEditorScreen"):
            run(mixin._edit_instructions_file(instance, instr_path, title="T", done_msg="saved!"))
        on_saved = instance.push_screen.await_args[0][1]

        on_saved(None)
        assert not instr_path.exists()
        instance.notify.assert_not_called()

        on_saved("new body")
        assert instr_path.read_text(encoding="utf-8") == "new body"
        instance.notify.assert_called_once_with("saved!")
        instance._refresh_project_state.assert_called_once()


class TestEditInExternalEditor:
    """``_edit_in_external_editor`` suspends the TUI and shells out to ``$EDITOR``."""

    def _get_mixin(self):
        """Import ProjectActionsMixin directly — avoids import_app() Textual stubs."""
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def _instance(self, mixin: object) -> mock.Mock:
        """Build a mixin mock with ``suspend`` wired as a context manager."""
        instance = mock.Mock(spec=mixin)
        instance.suspend = mock.MagicMock()
        instance._refresh_project_state = mock.Mock()
        instance.notify = mock.Mock()
        return instance

    def test_runs_editor_then_notifies_and_refreshes(self, tmp_path: object) -> None:
        """A clean editor exit creates the parent dir, notifies, and refreshes state."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instr_path = tmp_path / "nested" / "instructions.md"
        proc = mock.AsyncMock()
        with mock.patch(
            "terok.tui.project_actions.asyncio.create_subprocess_exec",
            new=mock.AsyncMock(return_value=proc),
        ) as exec_mock:
            run(mixin._edit_in_external_editor(instance, instr_path, "vim -u NONE", done_msg="ok"))
        exec_mock.assert_awaited_once_with("vim", "-u", "NONE", str(instr_path))
        proc.wait.assert_awaited_once()
        assert instr_path.parent.is_dir()
        instance.suspend.assert_called_once_with()
        instance.notify.assert_called_once_with("ok")
        instance._refresh_project_state.assert_called_once()

    def test_launch_failure_is_surfaced_not_raised(self, tmp_path: object) -> None:
        """A failed spawn prints an error and waits — it never crashes the TUI."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instr_path = tmp_path / "instructions.md"
        with (
            mock.patch(
                "terok.tui.project_actions.asyncio.create_subprocess_exec",
                new=mock.AsyncMock(side_effect=FileNotFoundError("no such editor")),
            ),
            mock.patch("builtins.input") as input_mock,
        ):
            run(mixin._edit_in_external_editor(instance, instr_path, "bogus-editor", done_msg="ok"))
        input_mock.assert_called_once()
        instance.notify.assert_not_called()
        instance._refresh_project_state.assert_not_called()


class TestActionAuth:
    """Per-project ``_action_auth`` and host-wide ``_action_auth_host_wide`` dispatch."""

    def _get_mixin(self):
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_per_project_dispatches_auth_with_project_id(self) -> None:
        """``_action_auth`` is the project-details path — passes the selection."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "myproj"
        run(mixin._action_auth(instance, "claude"))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:auth",
            "claude",
            "myproj",
            title="Authenticating claude for myproj",
            refresh=None,
        )

    def test_per_project_without_selection_notifies_and_skips(self) -> None:
        """Without a selection ``_action_auth`` is a no-op — host-wide path is separate."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = None
        # ``notify`` lives on the App parent, not the mixin spec — wire it
        # explicitly so the early-return path can call it without erroring.
        instance.notify = mock.Mock()

        run(mixin._action_auth(instance, "claude"))
        instance._run_console_action.assert_not_called()
        instance.notify.assert_called_once()

    def test_host_wide_dispatches_auth_with_none(self) -> None:
        """``_action_auth_host_wide`` ignores ``current_project_id`` by design."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "selected-but-irrelevant"
        run(mixin._action_auth_host_wide(instance, "claude"))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:auth",
            "claude",
            None,
            title="Authenticating claude (host-wide)",
            refresh=None,
        )


class TestActionSelection:
    """Tests for task selection after task creation flows."""

    def test_task_start_cli_selects_created_task(self) -> None:
        _, app_class = import_app()
        instance = make_creation_app(app_class)
        instance.dispatch_console_action = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        fake_task_new = mock.Mock(return_value="42")
        action_globals = app_class._start_cli_task_background.__globals__

        fake_project = mock.Mock()
        fake_project.default_login = None
        fake_load_project = mock.Mock(return_value=fake_project)

        with mock.patch.dict(
            action_globals,
            {
                "task_new": fake_task_new,
                "load_project": fake_load_project,
                "container_name": lambda *a: "terok-proj1-cli-42",
            },
        ):
            run(app_class._start_cli_task_background(instance, "test-name"))

        assert instance._last_selected_tasks.get("proj1") == "42"
        fake_task_new.assert_called_once_with("proj1", name="test-name")
        instance._save_selection_state.assert_called_once()
        # The container start is dispatched as a captured ConsoleLog action.
        instance.dispatch_console_action.assert_called_once()
        assert (
            instance.dispatch_console_action.call_args[0][0]
            == "terok.tui.worker_actions:start_cli_container"
        )
        instance.refresh_tasks.assert_awaited()

    def test_autopilot_launch_selects_created_task(self) -> None:
        app_mod, app_class = import_app()

        instance = app_class()
        instance.current_project_id = "proj1"
        instance._last_selected_tasks = {}
        instance.notify = mock.Mock()
        instance._save_selection_state = mock.Mock()
        instance._start_autopilot_watcher = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "autopilot-launch"
        worker.result = ("proj1", "123", None)
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        assert instance._last_selected_tasks.get("proj1") == "123"
        instance._save_selection_state.assert_called_once()
        instance._start_autopilot_watcher.assert_called_once_with("proj1", "123")
        instance.refresh_tasks.assert_awaited_once()


class TestGateSyncAction:
    """``_action_sync_gate`` dispatches the sync_gate worker action."""

    def _get_mixin(self):
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_action_sync_gate_dispatches_sync_gate(self) -> None:
        """With a selection, the action dispatches the sync_gate worker entrypoint."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "proj1"
        run(mixin._action_sync_gate(instance))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:sync_gate",
            "proj1",
            title="Syncing gate for proj1",
        )

    def test_action_sync_gate_without_selection_skips(self) -> None:
        """No project selected — the action notifies and dispatches nothing."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = None
        instance.notify = mock.Mock()
        run(mixin._action_sync_gate(instance))
        instance._run_console_action.assert_not_called()
        instance.notify.assert_called_once()


class TestProjectScreenNoneState:
    """Tests that ProjectDetailsScreen handles None state correctly."""

    def test_project_screen_stores_none_state(self) -> None:
        screens, _ = import_screens()
        project = make_project(id="proj1")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=3)
        assert screen._state is None
        assert screen._task_count == 3


class TestGateServerScreen:
    """Tests for the GateServerScreen."""

    def test_gate_server_screen_construction(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status()
        screen = screens.GateServerScreen(status)
        assert screen._status == status

    def test_gate_server_screen_construction_default(self) -> None:
        screens, _ = import_screens()
        screen = screens.GateServerScreen()
        assert screen._status is None

    def test_gate_server_screen_dismiss(self) -> None:
        screens, _ = import_screens()
        screen = screens.GateServerScreen()
        screen.dismiss = mock.Mock()
        run(screen.action_dismiss())
        screen.dismiss.assert_called_once_with(None)

    @pytest.mark.parametrize(
        ("method_name", "expected"),
        [
            pytest.param("action_gate_install", "gate_install", id="install"),
            pytest.param("action_gate_uninstall", "gate_uninstall", id="uninstall"),
            pytest.param("action_gate_start", "gate_start", id="start"),
            pytest.param("action_gate_stop", "gate_stop", id="stop"),
        ],
    )
    def test_gate_server_screen_actions(self, method_name: str, expected: str) -> None:
        screens, _ = import_screens()
        screen = screens.GateServerScreen()
        screen.dismiss = mock.Mock()
        getattr(screen, method_name)()
        screen.dismiss.assert_called_once_with(expected)


class TestDisableOptions:
    """Tests for the _disable_options helper used by systemd-dependent screens."""

    def test_disable_options_disables_matching_ids(self) -> None:
        """Options whose id is in the frozenset are disabled."""
        screens, _ = import_screens()
        option_list = mock.Mock()
        opts = [mock.Mock(id="a"), mock.Mock(id="b"), mock.Mock(id="c")]
        option_list.option_count = len(opts)
        option_list.get_option_at_index = lambda idx: opts[idx]

        screens._disable_options(option_list, frozenset({"a", "c"}))

        option_list.disable_option_at_index.assert_any_call(0)
        option_list.disable_option_at_index.assert_any_call(2)
        assert option_list.disable_option_at_index.call_count == 2

    def test_gate_server_systemd_option_ids(self) -> None:
        """GateServerScreen declares the correct systemd option ids."""
        screens, _ = import_screens()
        assert {"gate_install", "gate_uninstall"} == screens.GateServerScreen._SYSTEMD_OPTIONS

    def test_vault_systemd_option_ids(self) -> None:
        """VaultScreen declares the correct systemd option ids."""
        screens, _ = import_screens()
        assert {
            "vault_install",
            "vault_uninstall",
        } == screens.VaultScreen._SYSTEMD_OPTIONS


class TestCommandPalette:
    """Tests for command palette customization."""

    def test_get_system_commands_includes_gate_server(self) -> None:
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        # get_system_commands imports SystemCommand at call time, so we need
        # textual.app in sys.modules during the call.
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Git Gate Server" in titles

    def test_get_system_commands_includes_authenticate(self) -> None:
        """The host-wide auth flow is reachable from the command palette."""
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Authenticate agents and tools" in titles

    def test_get_system_commands_includes_set_default_agents(self) -> None:
        """The global agent default is reachable from the command palette."""
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Set default agents" in titles


class TestDefaultAgentsAction:
    """The command-palette entry that writes ``image.agents`` in config.yml."""

    def test_on_default_agents_result_writes_selection(self) -> None:
        """A non-None selection is delegated to set_global_image_agents and notified."""
        _, app_class = import_app()
        instance = mock.MagicMock()
        instance.notify = mock.Mock()
        with mock.patch("terok.lib.integrations.executor.set_global_image_agents") as write_mock:
            write_mock.return_value = MOCK_CONFIG_ROOT / "config.yml"
            run(app_class._on_default_agents_result(instance, "claude,vibe"))
        write_mock.assert_called_once_with("claude,vibe")
        instance.notify.assert_called_once()
        assert "claude,vibe" in instance.notify.call_args.args[0]

    def test_on_default_agents_result_cancel_is_noop(self) -> None:
        """``None`` from the modal must NOT touch config.yml."""
        _, app_class = import_app()
        instance = mock.MagicMock()
        instance.notify = mock.Mock()
        with mock.patch("terok.lib.integrations.executor.set_global_image_agents") as write_mock:
            run(app_class._on_default_agents_result(instance, None))
        write_mock.assert_not_called()
        instance.notify.assert_not_called()


class TestGlobalAuthBinding:
    """The top-level ``a`` shortcut + ``action_authenticate`` route."""

    def test_app_binds_a_to_authenticate(self) -> None:
        """``a`` on the main screen opens the auth modal — no project required."""
        _, app_class = import_app()
        bindings = {(b[0], b[1]) for b in app_class.BINDINGS}
        assert ("a", "authenticate") in bindings

    def test_action_authenticate_pushes_auth_actions_screen(self) -> None:
        """``action_authenticate`` opens [`AuthActionsScreen`][terok.tui.screens.AuthActionsScreen]."""
        _, app_class = import_app()
        # No spec — ``push_screen`` is inherited from the Textual ``App``
        # stub and isn't present on the inner ``TerokTUI`` class itself.
        instance = mock.MagicMock()
        instance.push_screen = mock.AsyncMock()
        run(app_class.action_authenticate(instance))
        instance.push_screen.assert_awaited_once()
        pushed_screen, callback = instance.push_screen.await_args.args
        assert type(pushed_screen).__name__ == "AuthActionsScreen"
        assert callback == instance._on_authenticate_result

    def test_on_authenticate_result_routes_to_host_wide(self) -> None:
        """``auth_<provider>`` from the global modal lands in the host-wide handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_authenticate_result(instance, "auth_claude"))
        instance._action_auth_host_wide.assert_awaited_once_with("claude")
        instance._action_auth.assert_not_called()

    def test_on_authenticate_result_routes_opencode_import(self) -> None:
        """OpenCode import from the global modal reuses the project-screen handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_authenticate_result(instance, "import_opencode_config"))
        instance._action_import_opencode_config.assert_awaited_once()

    def test_on_authenticate_result_ignores_cancel(self) -> None:
        """Esc on the modal returns ``None`` — handler is a no-op."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_authenticate_result(instance, None))
        instance._action_auth_host_wide.assert_not_called()
        instance._action_import_opencode_config.assert_not_called()


class TestRenderGateServerStatus:
    """Tests for the render_gate_server_status helper."""

    def test_render_gate_server_status_none(self) -> None:
        screens, _ = import_screens()
        result = screens.render_gate_server_status(None)
        assert isinstance(result, Text)
        assert "unknown" in str(result)

    def test_render_gate_server_status_running(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status()
        with mock.patch.object(screens, "check_units_outdated", return_value=None):
            result = screens.render_gate_server_status(status)
        text_str = str(result)
        assert "running" in text_str
        assert "systemd" in text_str
        assert str(GATE_PORT) in text_str

    def test_render_gate_server_status_stopped(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status(mode="none", running=False)
        with mock.patch.object(screens, "check_units_outdated", return_value=None):
            result = screens.render_gate_server_status(status)
        text_str = str(result)
        assert "stopped" in text_str
        assert "not running" in text_str

    def test_render_gate_server_status_outdated(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status()
        with mock.patch.object(
            screens, "check_units_outdated", return_value="Units outdated (v1 vs v3)"
        ):
            result = screens.render_gate_server_status(status)
        text_str = str(result)
        assert "outdated" in text_str


class TestCombinedGateStatus:
    """Tests for combined gate status in render_project_details."""

    def test_render_project_details_gate_server_down(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}
        gate_status = mock.Mock()
        gate_status.running = False

        result = widgets.render_project_details(
            project, state, task_count=5, gate_server_status=gate_status
        )
        text_str = str(result)
        assert "gate down" in text_str

    def test_render_project_details_gate_server_ok(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}
        gate_status = mock.Mock()
        gate_status.running = True

        result = widgets.render_project_details(
            project, state, task_count=5, gate_server_status=gate_status
        )
        text_str = str(result)
        assert "gate down" not in text_str
        assert "yes" in text_str

    def test_render_project_details_gate_server_none_fallback(self) -> None:
        """When gate_server_status is None, show normal repo-based status."""
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": False}

        result = widgets.render_project_details(project, state, task_count=5)
        text_str = str(result)
        assert "gate down" not in text_str


class TestGateServerActionDispatch:
    """Tests for gate server action dispatch routing."""

    @pytest.mark.parametrize(("action", "handler"), _gate_server_action_cases())
    def test_gate_server_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every entry in GATE_SERVER_ACTION_HANDLERS routes to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_gate_server_action_result(instance, action))
        getattr(instance, handler).assert_called_once()

    def test_gate_server_action_dispatch_none(self) -> None:
        """None result does not dispatch any handler."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_gate_server_action_result(instance, None))
        # No action handler should have been called
        for handler in app_mod.GATE_SERVER_ACTION_HANDLERS.values():
            getattr(instance, handler).assert_not_called()


class TestDeleteTaskResult:
    """Tests for _delete_task tuple shape and delete notification messages."""

    def _call_delete(
        self,
        side_effect: BaseException | None = None,
        warnings: list[str] | None = None,
        **kwargs: str,
    ) -> tuple[str, str, str, str | None, list[str]]:
        """Import app, mock task_delete, and call _delete_task."""
        from terok.lib.orchestration.tasks import TaskDeleteResult

        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        # Patch task_delete in the method's own globals (the reimported module dict).
        fn_globals = app_class._delete_task.__globals__
        orig = fn_globals["task_delete"]
        if side_effect:
            fake = mock.Mock(side_effect=side_effect)
        else:
            task_id = kwargs.get("task_id", "3")
            fake = mock.Mock(
                return_value=TaskDeleteResult(task_id=task_id, warnings=warnings or [])
            )
        fn_globals["task_delete"] = fake
        try:
            return app_class._delete_task(
                instance,
                kwargs.get("project_id", "proj1"),
                kwargs.get("task_id", "3"),
                kwargs.get("task_name", "fix-login"),
            )
        finally:
            fn_globals["task_delete"] = orig

    def test_delete_task_success_returns_five_tuple(self) -> None:
        """Successful deletion returns (project_id, task_id, task_name, None, [])."""
        assert self._call_delete() == ("proj1", "3", "fix-login", None, [])

    def test_delete_task_error_returns_five_tuple(self) -> None:
        """Failed deletion returns error string and empty warnings."""
        result = self._call_delete(side_effect=RuntimeError("boom"))
        assert result == ("proj1", "3", "fix-login", "boom", [])

    def test_delete_task_systemexit_returns_five_tuple(self) -> None:
        """SystemExit during deletion is captured in the error slot."""
        result = self._call_delete(side_effect=SystemExit("not found"), task_name="")
        assert result == ("proj1", "3", "", "not found", [])

    def test_delete_task_empty_name(self) -> None:
        """Empty task name is preserved through the round-trip."""
        result = self._call_delete(task_name="")
        assert result == ("proj1", "3", "", None, [])

    def test_delete_task_warnings_propagated(self) -> None:
        """Warnings from TaskDeleteResult are passed through the tuple."""
        result = self._call_delete(warnings=["Container c1: locked"])
        assert result == ("proj1", "3", "fix-login", None, ["Container c1: locked"])


# ---------------------------------------------------------------------------
# Vault Screen
# ---------------------------------------------------------------------------

MOCK_VAULT_SOCKET = MOCK_BASE / "run" / "vault.sock"
MOCK_VAULT_DB = MOCK_BASE / "vault" / "credentials.db"
MOCK_VAULT_ROUTES = MOCK_BASE / "vault" / "routes.json"


def make_vault_status(
    *,
    mode: str = "daemon",
    running: bool = True,
    transport: str | None = None,
    routes_configured: int = 3,
    credentials_stored: tuple[str, ...] = ("claude", "gh"),
    ssh_keys_stored: int = 0,
    passphrase_source: str | None = "keyring",
    locked: bool = False,
    plaintext_passphrase_path: object | None = None,
) -> mock.Mock:
    """Build a vault status mock with common defaults.

    The post-#278 / #282 fields are set explicitly so ``mock.Mock``
    truthiness doesn't accidentally trip the locked branch or the
    plaintext-warning branch in
    [`render_vault_status`][terok.tui.screens.render_vault_status].

    ``transport`` defaults to ``None`` so existing tests get the
    ``(not configured)`` rendering instead of leaking a ``Mock`` repr.
    Tests that exercise the TCP / socket port surfacing pass
    ``transport="tcp"`` / ``"socket"`` explicitly.
    """
    status = mock.Mock()
    status.mode = mode
    status.running = running
    status.transport = transport
    status.socket_path = MOCK_VAULT_SOCKET
    status.db_path = MOCK_VAULT_DB
    status.routes_path = MOCK_VAULT_ROUTES
    status.routes_configured = routes_configured
    status.credentials_stored = credentials_stored
    status.ssh_keys_stored = ssh_keys_stored
    status.passphrase_source = passphrase_source
    status.locked = locked
    status.plaintext_passphrase_path = plaintext_passphrase_path
    return status


class TestVaultScreen:
    """Tests for the VaultScreen."""

    def test_vault_screen_construction(self) -> None:
        """Screen stores the provided status."""
        screens, _ = import_screens()
        status = make_vault_status()
        screen = screens.VaultScreen(status)
        assert screen._status == status

    def test_vault_screen_construction_default(self) -> None:
        """Screen defaults to None status."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        assert screen._status is None

    def test_vault_screen_dismiss(self) -> None:
        """action_dismiss sends None result."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        screen.dismiss = mock.Mock()
        run(screen.action_dismiss())
        screen.dismiss.assert_called_once_with(None)

    @pytest.mark.parametrize(
        ("method_name", "expected"),
        [
            pytest.param("action_vault_install", "vault_install", id="install"),
            pytest.param("action_vault_uninstall", "vault_uninstall", id="uninstall"),
            pytest.param("action_vault_start", "vault_start", id="start"),
            pytest.param("action_vault_stop", "vault_stop", id="stop"),
            pytest.param("action_vault_unlock", "vault_unlock", id="unlock"),
            pytest.param("action_vault_lock", "vault_lock", id="lock"),
            pytest.param("action_vault_seal", "vault_seal", id="seal"),
            pytest.param("action_vault_to_keyring", "vault_to_keyring", id="to-keyring"),
        ],
    )
    def test_vault_screen_actions(self, method_name: str, expected: str) -> None:
        """Action methods dismiss with the expected result string."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        screen.dismiss = mock.Mock()
        getattr(screen, method_name)()
        screen.dismiss.assert_called_once_with(expected)


class TestRenderVaultStatus:
    """Tests for the render_vault_status helper."""

    def test_render_vault_status_none(self) -> None:
        """None status renders an 'unknown' message."""
        screens, _ = import_screens()
        result = screens.render_vault_status(None)
        assert isinstance(result, Text)
        assert "unknown" in str(result)

    def test_render_vault_status_running(self) -> None:
        """Running vault shows status and credential details."""
        screens, _ = import_screens()
        status = make_vault_status()
        result = screens.render_vault_status(status)
        text_str = str(result)
        assert "running" in text_str
        assert "claude" in text_str
        assert "3 configured" in text_str

    def test_render_vault_status_stopped(self) -> None:
        """Stopped vault shows hint text."""
        screens, _ = import_screens()
        status = make_vault_status(running=False)
        result = screens.render_vault_status(status)
        text_str = str(result)
        assert "stopped" in text_str
        assert "actions below" in text_str

    def test_render_vault_status_standby(self) -> None:
        """Systemd socket active but service idle shows standby."""
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", running=False)
        with mock.patch("terok.lib.integrations.sandbox.is_vault_socket_active", return_value=True):
            result = screens.render_vault_status(status)
        text_str = str(result)
        assert "standby" in text_str
        assert "first connection" in text_str
        # Standby should not show the "actions below" help text
        assert "actions below" not in text_str

    def test_render_vault_status_systemd_stopped(self) -> None:
        """Systemd socket inactive shows stopped with help text."""
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", running=False)
        with mock.patch(
            "terok.lib.integrations.sandbox.is_vault_socket_active", return_value=False
        ):
            result = screens.render_vault_status(status)
        text_str = str(result)
        assert "stopped" in text_str
        assert "actions below" in text_str

    def test_render_vault_status_no_credentials(self) -> None:
        """Empty credentials tuple renders 'none stored'."""
        screens, _ = import_screens()
        status = make_vault_status(credentials_stored=())
        result = screens.render_vault_status(status)
        assert "none stored" in str(result)

    def test_render_vault_status_shows_passphrase_source(self) -> None:
        """Resolved tier surfaces as ``Passphrase: resolved via <source>``."""
        screens, _ = import_screens()
        status = make_vault_status(passphrase_source="systemd-creds", ssh_keys_stored=3)
        text_str = str(screens.render_vault_status(status))
        assert "resolved via systemd-creds" in text_str
        assert "SSH keys:    3" in text_str

    def test_render_vault_status_announces_locked(self) -> None:
        """Locked vault prints an explicit ``Locked: yes`` line with the no-tier reason."""
        screens, _ = import_screens()
        status = make_vault_status(locked=True, passphrase_source=None)
        text_str = str(screens.render_vault_status(status))
        assert "Locked:" in text_str
        assert "yes" in text_str
        assert "no tier resolved" in text_str
        # And when locked, the Passphrase: line is suppressed (no tier to name).
        assert "resolved via" not in text_str

    def test_render_vault_status_marks_unlocked_explicitly(self) -> None:
        """Resolved vault shows ``Locked: no`` plus which tier did it."""
        screens, _ = import_screens()
        status = make_vault_status(locked=False, passphrase_source="keyring")
        text_str = str(screens.render_vault_status(status))
        assert "Locked:      no" in text_str
        assert "resolved via keyring" in text_str

    def test_render_vault_status_surfaces_plaintext_warning(self) -> None:
        """``plaintext_passphrase_path`` set → red WARNING line shows the basename only.

        Aisle CR (terok#939, CWE-200): the TUI is screenshot- and
        screen-share friendly, so rendering the full filesystem path
        is more disclosure than the warning requires.  Surface the
        basename — enough to recognise *which* file, not enough to
        advertise its location.  The CLI ``vault status`` keeps
        printing the full path for grep-friendly scripting.
        """
        screens, _ = import_screens()
        config_path = MOCK_BASE / "etc" / "terok" / "config.yml"
        status = make_vault_status(plaintext_passphrase_path=config_path)
        text_str = str(screens.render_vault_status(status))
        assert "WARNING" in text_str
        assert "plaintext" in text_str
        # Basename surfaces; the full path does not.
        assert "config.yml" in text_str
        assert str(config_path.parent) not in text_str

    def test_render_vault_status_no_plaintext_warning_when_unset(self) -> None:
        """Default-None case keeps the render quiet — no WARNING line at all."""
        screens, _ = import_screens()
        status = make_vault_status(plaintext_passphrase_path=None)
        text_str = str(screens.render_vault_status(status))
        assert "WARNING" not in text_str
        assert "plaintext" not in text_str

    def test_render_vault_status_renames_mode_to_activation(self) -> None:
        """The legacy ``Mode:`` label is gone — lifecycle now reads as ``Activation:``.

        Guards the rename so a future regression that re-introduces the
        old label gets caught here.  The two TUI / CLI views are kept in
        lockstep (executor#356), so a one-sided revert would leave the
        operator looking at two different vocabularies.
        """
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", transport=None)
        text_str = str(screens.render_vault_status(status))
        assert "Activation:  systemd" in text_str
        assert "Mode:        " not in text_str

    def test_render_vault_status_tcp_surfaces_ports_and_annotates_socket(self) -> None:
        """TCP mode surfaces ``TCP broker:`` / ``TCP signer:`` and tags ``Socket:``.

        Mirrors the executor CLI behaviour from terok-ai/terok-executor#356:
        the daemon still binds ``vault.sock`` as a local fast-path probe
        target but no client traffic touches it, so surfacing the TCP
        listeners and annotating the lingering Unix line is what keeps
        operator-readable.
        """
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", transport="tcp")
        with (
            mock.patch(
                "terok.lib.integrations.sandbox.get_token_broker_port",
                return_value=18701,
            ),
            mock.patch(
                "terok.lib.integrations.sandbox.get_ssh_signer_port",
                return_value=18702,
            ),
        ):
            text_str = str(screens.render_vault_status(status))
        assert "Transport:   tcp" in text_str
        assert "TCP broker:  127.0.0.1:18701" in text_str
        assert "TCP signer:  127.0.0.1:18702" in text_str
        assert "(fast-path probe only)" in text_str

    def test_render_vault_status_socket_surfaces_signer_socket(self, tmp_path) -> None:
        """Socket mode surfaces the SSH signer socket alongside the broker socket.

        Both bind-mounted endpoints (broker + signer) appear in the
        rendered block so the TUI matches what containers actually see
        per the executor env wiring.  TCP-only embellishments must stay
        absent.
        """
        from terok_sandbox import SandboxConfig

        cfg = SandboxConfig(
            state_dir=tmp_path / "state",
            runtime_dir=tmp_path / "run",
            vault_dir=tmp_path / "vault",
            services_mode="socket",
        )
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", transport="socket")
        with mock.patch("terok.lib.api.make_sandbox_config", return_value=cfg):
            text_str = str(screens.render_vault_status(status))
        assert "Transport:   socket" in text_str
        assert f"SSH signer:  {cfg.ssh_signer_socket_path}" in text_str
        # TCP-only embellishments stay out of the socket-mode render.
        assert "TCP broker:" not in text_str
        assert "TCP signer:" not in text_str
        assert "(fast-path probe only)" not in text_str

    def test_render_vault_status_unknown_transport_falls_back(self) -> None:
        """``status.transport is None`` (or unexpected) renders ``(not configured)``.

        Also guards the allow-list against any future widening that
        might leak a raw ``Mock`` / repr into operator-facing output.
        """
        screens, _ = import_screens()
        status = make_vault_status(mode="none", running=False, transport=None)
        text_str = str(screens.render_vault_status(status))
        assert "Transport:   (not configured)" in text_str
        assert "TCP broker:" not in text_str
        assert "SSH signer:" not in text_str


class TestVaultUnlockModal:
    """Behaviour of the [`VaultUnlockModal`][terok.tui.screens.VaultUnlockModal] passphrase prompt."""

    def test_action_cancel_dismisses_none(self) -> None:
        """``escape`` / Cancel binding hands ``None`` to the result callback."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        modal.action_cancel()
        modal.dismiss.assert_called_once_with(None)

    def test_button_cancel_dismisses_none(self) -> None:
        """Cancel button id routes to ``dismiss(None)``."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "vault-unlock-cancel"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(None)

    def test_button_ok_dismisses_input_value(self) -> None:
        """Unlock button reads the masked Input and dismisses with the value."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        mock_input = mock.Mock()
        mock_input.value = "hunter2"
        modal.query_one = mock.Mock(return_value=mock_input)
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "vault-unlock-ok"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with("hunter2")

    def test_button_ok_with_empty_value_dismisses_none(self) -> None:
        """Empty masked Input collapses to ``None`` so the caller skips the write."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        mock_input = mock.Mock()
        mock_input.value = ""
        modal.query_one = mock.Mock(return_value=mock_input)
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "vault-unlock-ok"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(None)

    def test_button_unknown_id_is_ignored(self) -> None:
        """Stray button events from re-used CSS / future expansion don't dismiss."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "not-a-vault-button"
        modal.on_button_pressed(event)
        modal.dismiss.assert_not_called()

    def test_input_submitted_with_value_dismisses_value(self) -> None:
        """Pressing Enter inside the masked input behaves like clicking Unlock."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.value = "swordfish"
        modal.on_input_submitted(event)
        modal.dismiss.assert_called_once_with("swordfish")

    def test_input_submitted_empty_dismisses_none(self) -> None:
        """Enter on an empty input dismisses ``None`` (treated as Cancel)."""
        screens, _ = import_screens()
        modal = screens.VaultUnlockModal()
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.value = ""
        modal.on_input_submitted(event)
        modal.dismiss.assert_called_once_with(None)


class TestVaultRevealModal:
    """Behaviour of [`VaultRevealModal`][terok.tui.screens.VaultRevealModal]."""

    def _modal(self, screens, *, already_acked: bool = False):  # type: ignore[no-untyped-def]
        return screens.VaultRevealModal(
            "correct-horse-battery-staple", "keyring", already_acked=already_acked
        )

    def test_action_cancel_dismisses_none(self) -> None:
        """``escape`` is a soft close — leaves the marker untouched."""
        screens, _ = import_screens()
        modal = self._modal(screens)
        modal.dismiss = mock.Mock()
        modal.action_cancel()
        modal.dismiss.assert_called_once_with(None)

    def test_button_ack_dismisses_true(self) -> None:
        """``Mark as saved`` → dismiss(True) so the caller writes the marker."""
        screens, _ = import_screens()
        modal = self._modal(screens)
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "vault-reveal-ack"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(True)

    def test_button_already_acked_dismisses_none(self) -> None:
        """``Already marked saved`` is informational only — no state change."""
        screens, _ = import_screens()
        modal = self._modal(screens, already_acked=True)
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "vault-reveal-acked"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(None)

    def test_button_close_dismisses_false(self) -> None:
        """``Close`` is explicit decline — dismiss(False) so the caller treats it as such."""
        screens, _ = import_screens()
        modal = self._modal(screens)
        modal.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "vault-reveal-cancel"
        modal.on_button_pressed(event)
        modal.dismiss.assert_called_once_with(False)

    def test_already_acked_flag_round_trips(self) -> None:
        """The flag is observable on the constructed modal — used by callers/tests."""
        screens, _ = import_screens()
        assert self._modal(screens, already_acked=False)._already_acked is False
        assert self._modal(screens, already_acked=True)._already_acked is True


class TestVaultScreenRefresh:
    """Tests for vault screen refresh logic."""

    def test_refresh_status_updates_status(self) -> None:
        """_refresh_status fetches new status from terok_sandbox."""
        screens, _ = import_screens()
        screen = screens.VaultScreen(make_vault_status(running=False))
        detail = mock.Mock()
        screen.query_one = mock.Mock(return_value=detail)
        new_status = make_vault_status(running=True)
        with mock.patch("terok.lib.integrations.sandbox.get_vault_status", return_value=new_status):
            screen._refresh_status()
        assert screen._status is new_status
        detail.update.assert_called_once()

    def test_refresh_status_handles_exception(self) -> None:
        """_refresh_status sets status to None on failure."""
        screens, _ = import_screens()
        screen = screens.VaultScreen(make_vault_status())
        detail = mock.Mock()
        screen.query_one = mock.Mock(return_value=detail)
        with mock.patch(
            "terok.lib.integrations.sandbox.get_vault_status", side_effect=RuntimeError
        ):
            screen._refresh_status()
        assert screen._status is None

    def test_vault_screen_refresh_action(self) -> None:
        """action_vault_refresh calls _refresh_status."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        screen._refresh_status = mock.Mock()
        screen.action_vault_refresh()
        screen._refresh_status.assert_called_once()


class TestVaultCommandPalette:
    """Tests for vault in the command palette."""

    def test_get_system_commands_includes_vault(self) -> None:
        """Command palette includes 'Vault' entry."""
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Vault" in titles


class TestVaultActionDispatch:
    """Tests for vault action handler dispatch."""

    @pytest.mark.parametrize(
        ("action", "handler"),
        [
            ("vault_install", "_action_vault_install"),
            ("vault_uninstall", "_action_vault_uninstall"),
            ("vault_start", "_action_vault_start"),
            ("vault_stop", "_action_vault_stop"),
            ("vault_unlock", "_action_vault_unlock"),
            ("vault_lock", "_action_vault_lock"),
            ("vault_seal", "_action_vault_seal"),
            ("vault_to_keyring", "_action_vault_to_keyring"),
            ("vault_reveal", "_action_vault_reveal"),
            ("vault_acknowledge", "_action_vault_acknowledge"),
        ],
    )
    def test_vault_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every vault action routes through the callback to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_vault_action_result(instance, action))
        getattr(instance, handler).assert_called_once()

    def test_vault_action_dispatch_none(self) -> None:
        """None result does not dispatch any handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_vault_action_result(instance, None))
        instance._action_vault_install.assert_not_called()
        instance._action_vault_uninstall.assert_not_called()
        instance._action_vault_start.assert_not_called()
        instance._action_vault_stop.assert_not_called()


class TestVaultActionImplementations:
    """Verify the unlock / lock / seal handlers do the right thing under the hood.

    The dispatch tests above prove the chooser callback routes to the
    right method.  These tests prove the methods themselves invoke the
    correct sandbox/system entry points — together they cover the
    chain from VaultScreen selection through to side effects.
    """

    def _get_mixin(self) -> type:
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_unlock_pushes_modal_with_result_callback(self) -> None:
        """``_action_vault_unlock`` opens VaultUnlockModal wired to ``_on_vault_unlock_result``."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.push_screen = mock.AsyncMock()
        # ``_on_vault_unlock_result`` lives on the composed App, not on the
        # mixin's TYPE_CHECKING stubs the spec sees — wire it explicitly so
        # we can compare against the value passed as the callback.
        instance._on_vault_unlock_result = mock.AsyncMock()
        run(mixin._action_vault_unlock(instance))
        instance.push_screen.assert_awaited_once()
        modal_arg, callback_arg = instance.push_screen.call_args[0]
        # The handler imports VaultUnlockModal from the real ``terok.tui.screens``
        # module, distinct from the textual-stubbed copy ``import_screens``
        # produces — compare by class name rather than identity.
        assert type(modal_arg).__name__ == "VaultUnlockModal"
        assert callback_arg is instance._on_vault_unlock_result

    def test_lock_dispatches_vault_lock(self) -> None:
        """``_action_vault_lock`` dispatches the vault_lock worker action + refreshes status."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        run(mixin._action_vault_lock(instance))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:vault_lock",
            title="Locking vault (clearing session tier)",
            refresh="vault_status",
        )

    def test_seal_dispatches_vault_seal(self) -> None:
        """``_action_vault_seal`` dispatches the vault_seal worker action + refreshes status."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        run(mixin._action_vault_seal(instance))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:vault_seal",
            title="Sealing vault passphrase into systemd-creds",
            refresh="vault_status",
        )

    def test_to_keyring_dispatches_vault_to_keyring(self) -> None:
        """``_action_vault_to_keyring`` dispatches the worker action + refreshes status."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        run(mixin._action_vault_to_keyring(instance))
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:vault_to_keyring",
            title="Moving vault passphrase to OS keyring",
            refresh="vault_status",
        )


class TestVaultRevealAction:
    """``_action_vault_reveal`` pushes the reveal modal via the callback pattern.

    Uses ``push_screen(modal, callback)`` rather than
    ``push_screen_wait()`` because the action runs from the vault
    chooser's ``_on_vault_action_result`` callback, which is not a
    worker context — ``push_screen_wait`` raises ``NoActiveWorker``
    there.  The dismissal value flows through
    ``_on_vault_reveal_result`` (separate test class below).
    """

    def _get_mixin(self) -> type:
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def _make_cfg(self, *, passphrase: str | None = "p4ss", source: str | None = "keyring"):
        cfg = mock.Mock()
        cfg.resolve_passphrase_with_source.return_value = (passphrase, source)
        return cfg

    def test_locked_vault_notifies_and_skips_modal(self) -> None:
        """No resolvable passphrase → notify, no modal."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: self._make_cfg(passphrase=None),
                NoPassphraseError=Exception,
                WrongPassphraseError=Exception,
                is_recovery_acknowledged=mock.Mock(return_value=False),
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_reveal(instance))
        instance.notify.assert_called_once()
        # "unlock first" hint surfaces.
        assert "unlock" in instance.notify.call_args[0][0].lower()
        instance.push_screen.assert_not_called()

    def test_resolver_raises_translates_to_error_notify(self) -> None:
        """``WrongPassphraseError`` from resolver → error notification, no modal."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen = mock.AsyncMock()

        class _Wrong(Exception):
            pass

        cfg = mock.Mock()
        cfg.resolve_passphrase_with_source.side_effect = _Wrong("wrong key")
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: cfg,
                NoPassphraseError=_Wrong,
                WrongPassphraseError=_Wrong,
                is_recovery_acknowledged=mock.Mock(return_value=False),
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_reveal(instance))
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs["severity"] == "error"
        instance.push_screen.assert_not_called()

    def test_resolved_pushes_reveal_modal_with_callback(self) -> None:
        """Resolved passphrase → modal pushed via callback pattern (not push_screen_wait)."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: self._make_cfg(),
                NoPassphraseError=Exception,
                WrongPassphraseError=Exception,
                is_recovery_acknowledged=mock.Mock(return_value=False),
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_reveal(instance))
        instance.push_screen.assert_awaited_once()
        modal_arg, callback_arg = instance.push_screen.call_args[0]
        assert type(modal_arg).__name__ == "VaultRevealModal"
        # Callback is the bound ``_on_vault_reveal_result`` — pinning
        # the wiring catches regressions to inline (worker-required)
        # ``push_screen_wait`` calls.
        assert callback_arg is instance._on_vault_reveal_result

    def test_resolved_passes_already_acked_flag_to_modal(self) -> None:
        """The modal is told whether the marker already matches."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        instance._refresh_vault_status = mock.AsyncMock()
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: self._make_cfg(),
                NoPassphraseError=Exception,
                WrongPassphraseError=Exception,
                is_recovery_acknowledged=mock.Mock(return_value=True),
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_reveal(instance))
        modal_arg = instance.push_screen.call_args[0][0]
        # The modal stashes the already_acked flag as ``_already_acked``.
        assert getattr(modal_arg, "_already_acked", False) is True


class TestVaultRevealResult:
    """``_on_vault_reveal_result`` handles the three dismissal outcomes."""

    def _get_mixin(self) -> type:
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_true_outcome_acks_and_refreshes(self) -> None:
        """Operator clicked Mark-as-saved → ack lands, pill refresh fires."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance._refresh_vault_status = mock.AsyncMock()
        ack_recovery = mock.Mock(return_value=True)
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: mock.Mock(),
                acknowledge_recovery=ack_recovery,
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._on_vault_reveal_result(instance, True))
        ack_recovery.assert_called_once()
        instance._refresh_vault_status.assert_awaited_once()

    def test_false_outcome_does_nothing(self) -> None:
        """Operator clicked Close → no ack, no refresh."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance._refresh_vault_status = mock.AsyncMock()
        ack_recovery = mock.Mock(return_value=True)
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: mock.Mock(),
                acknowledge_recovery=ack_recovery,
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._on_vault_reveal_result(instance, False))
        ack_recovery.assert_not_called()
        instance._refresh_vault_status.assert_not_awaited()

    def test_none_outcome_does_nothing(self) -> None:
        """Esc / already-acked dialog → no state change."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance._refresh_vault_status = mock.AsyncMock()
        ack_recovery = mock.Mock(return_value=True)
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: mock.Mock(),
                acknowledge_recovery=ack_recovery,
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._on_vault_reveal_result(instance, None))
        ack_recovery.assert_not_called()
        instance._refresh_vault_status.assert_not_awaited()


class TestVaultAcknowledgeAction:
    """``_action_vault_acknowledge`` — silent ack from the screen."""

    def _get_mixin(self) -> type:
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_acknowledge_success_notifies_and_refreshes(self) -> None:
        """A successful ack notifies + triggers a pill refresh."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance._refresh_vault_status = mock.AsyncMock()
        ack_recovery = mock.Mock(return_value=True)
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: mock.Mock(),
                acknowledge_recovery=ack_recovery,
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_acknowledge(instance))
        ack_recovery.assert_called_once()
        instance.notify.assert_called_once()
        assert "marked as saved" in instance.notify.call_args[0][0]
        instance._refresh_vault_status.assert_awaited_once()

    def test_acknowledge_locked_notifies_warning(self) -> None:
        """Locked vault → wrapper returns False, surface warning, no refresh."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance._refresh_vault_status = mock.AsyncMock()
        ack_recovery = mock.Mock(return_value=False)
        stubs = {
            "terok.lib.integrations.sandbox": mock.Mock(
                SandboxConfig=lambda: mock.Mock(),
                acknowledge_recovery=ack_recovery,
            ),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_acknowledge(instance))
        ack_recovery.assert_called_once()
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs["severity"] == "warning"
        instance._refresh_vault_status.assert_not_awaited()


class TestMaybeWarnRecoveryUnconfirmed:
    """One-shot startup notification for an unconfirmed recovery key.

    Three severity bands — silent / yellow warning / red error — picked
    by ``RecoveryStatus.urgent`` so the message escalates when the
    operator is one reboot away from losing the vault.
    """

    @staticmethod
    def _fake_status(*, acknowledged: bool, source: object) -> object:
        """Build a duck-typed ``RecoveryStatus`` stand-in for the notify branches."""
        from terok.lib.integrations.sandbox import RecoveryStatus

        return RecoveryStatus(acknowledged=acknowledged, source=source)

    def test_warns_once_when_unlocked_and_unacked_durable(self) -> None:
        """Fresh process + unlocked vault + missing marker + durable tier → ``warning``."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = make_vault_status(locked=False, passphrase_source="keyring")
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        with mock.patch.object(
            app_mod,
            "recovery_status",
            return_value=self._fake_status(acknowledged=False, source="keyring"),
        ):
            app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs["severity"] == "warning"

    def test_errors_when_session_only(self) -> None:
        """Unacked + session-file source → red ``error`` (one reboot away from loss)."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = make_vault_status(
            locked=False, passphrase_source="session-file"
        )
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        with mock.patch.object(
            app_mod,
            "recovery_status",
            return_value=self._fake_status(acknowledged=False, source="session-file"),
        ):
            app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs["severity"] == "error"
        body = instance.notify.call_args[0][0]
        assert "UNRECOVERABLE" in body
        assert "reboot" in body.lower()

    def test_quiet_when_already_acknowledged(self) -> None:
        """Marker already lands → silent, no notification."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = make_vault_status(locked=False, passphrase_source="keyring")
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        with mock.patch.object(
            app_mod,
            "recovery_status",
            return_value=self._fake_status(acknowledged=True, source="keyring"),
        ):
            app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_not_called()

    def test_quiet_when_locked(self) -> None:
        """Locked vault → unlock modal already pulls attention; suppress warning."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = make_vault_status(locked=True, passphrase_source=None)
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        with mock.patch.object(
            app_mod,
            "recovery_status",
            return_value=self._fake_status(acknowledged=False, source=None),
        ):
            app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_not_called()

    def test_fires_at_most_once_per_session(self) -> None:
        """Second invocation does nothing — the flag survives the call."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = make_vault_status(locked=False, passphrase_source="keyring")
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        with mock.patch.object(
            app_mod,
            "recovery_status",
            return_value=self._fake_status(acknowledged=False, source="keyring"),
        ):
            app_class._maybe_warn_recovery_unconfirmed(instance)
            app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_called_once()

    def test_quiet_when_status_is_none(self) -> None:
        """No probe yet (status=None) → nothing to warn about."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = None
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        with mock.patch.object(
            app_mod,
            "recovery_status",
            return_value=self._fake_status(acknowledged=False, source="keyring"),
        ):
            app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_not_called()


class TestSelinuxFixDispatch:
    """``_run_setup_subprocess`` branches on exit code 5; ``_offer_selinux_fix`` routes outcomes."""

    def _entry(self, *, exit_code: int) -> mock.Mock:
        entry = mock.Mock()
        entry.ok = exit_code == 0
        entry.exit_code = exit_code
        entry.wait = mock.AsyncMock()
        return entry

    def test_exit_5_invokes_offer_selinux_fix(self) -> None:
        """``_run_setup_subprocess`` routes exit code 5 into ``_offer_selinux_fix``."""
        _, app_class = import_app()
        # Plain Mock (no spec) — the methods we exercise touch
        # ``self.notify`` and ``self.push_screen`` from Textual's App
        # base, which a spec'd mock doesn't auto-include.
        instance = mock.Mock()
        instance.dispatch_console_command.return_value = self._entry(exit_code=5)
        instance.push_screen = mock.AsyncMock()
        instance._offer_selinux_fix = mock.AsyncMock(return_value=True)
        result = run(app_class._run_setup_subprocess(instance))
        assert result is True
        instance._offer_selinux_fix.assert_awaited_once()

    def test_exit_nonzero_non_5_skips_selinux_fix(self) -> None:
        """A generic phase failure (exit 1) notifies + returns False — no SELinux modal."""
        _, app_class = import_app()
        instance = mock.Mock()
        instance.dispatch_console_command.return_value = self._entry(exit_code=1)
        instance.push_screen = mock.AsyncMock()
        instance._offer_selinux_fix = mock.AsyncMock()
        result = run(app_class._run_setup_subprocess(instance))
        assert result is False
        instance.notify.assert_called_once()
        instance._offer_selinux_fix.assert_not_awaited()

    def test_exit_0_returns_true_without_selinux_fix(self) -> None:
        """Happy path: exit 0 returns True, never opens the modal."""
        _, app_class = import_app()
        instance = mock.Mock()
        instance.dispatch_console_command.return_value = self._entry(exit_code=0)
        instance.push_screen = mock.AsyncMock()
        instance._offer_selinux_fix = mock.AsyncMock()
        result = run(app_class._run_setup_subprocess(instance))
        assert result is True
        instance._offer_selinux_fix.assert_not_awaited()

    def test_offer_selinux_fix_install_dispatches_then_reruns_setup(self) -> None:
        """Install button → dispatches selinux_install_policy worker + re-runs setup."""
        from terok.tui.selinux_fix_screen import SelinuxFixOutcome

        _, app_class = import_app()
        instance = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value=SelinuxFixOutcome.INSTALL_POLICY)
        instance.push_screen = mock.AsyncMock()
        instance.dispatch_console_command.return_value = self._entry(exit_code=0)
        instance._run_setup_subprocess = mock.AsyncMock(return_value=True)
        result = run(app_class._offer_selinux_fix(instance))
        assert result is True
        # The install worker was dispatched (title gives away the branch).
        title = instance.dispatch_console_command.call_args.kwargs["title"]
        assert "Installing SELinux policy" in title
        instance._run_setup_subprocess.assert_awaited_once()

    def test_offer_selinux_fix_tcp_dispatches_then_reruns_setup(self) -> None:
        """TCP-mode button → dispatches selinux_switch_to_tcp worker + re-runs setup."""
        from terok.tui.selinux_fix_screen import SelinuxFixOutcome

        _, app_class = import_app()
        instance = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value=SelinuxFixOutcome.SWITCH_TO_TCP)
        instance.push_screen = mock.AsyncMock()
        instance.dispatch_console_command.return_value = self._entry(exit_code=0)
        instance._run_setup_subprocess = mock.AsyncMock(return_value=True)
        result = run(app_class._offer_selinux_fix(instance))
        assert result is True
        title = instance.dispatch_console_command.call_args.kwargs["title"]
        assert "Switching services.mode" in title

    def test_offer_selinux_fix_skipped_returns_false_no_dispatch(self) -> None:
        """Skip → notifies + returns False; no worker dispatched, no setup re-run."""
        from terok.tui.selinux_fix_screen import SelinuxFixOutcome

        _, app_class = import_app()
        instance = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value=SelinuxFixOutcome.SKIPPED)
        instance._run_setup_subprocess = mock.AsyncMock()
        result = run(app_class._offer_selinux_fix(instance))
        assert result is False
        instance.notify.assert_called_once()
        instance.dispatch_console_command.assert_not_called()
        instance._run_setup_subprocess.assert_not_awaited()

    def test_offer_selinux_fix_remediation_failure_returns_false(self) -> None:
        """If the remediation worker fails (exit != 0), setup is not re-run."""
        from terok.tui.selinux_fix_screen import SelinuxFixOutcome

        _, app_class = import_app()
        instance = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value=SelinuxFixOutcome.INSTALL_POLICY)
        instance.push_screen = mock.AsyncMock()
        instance.dispatch_console_command.return_value = self._entry(exit_code=1)
        instance._run_setup_subprocess = mock.AsyncMock()
        result = run(app_class._offer_selinux_fix(instance))
        assert result is False
        instance._run_setup_subprocess.assert_not_awaited()


class TestVaultStatusPill:
    """Bottom-of-app StatusBar pill driven by [`_render_status_pill`][terok.tui.app.TerokTUI._render_status_pill]."""

    def test_render_pill_locked(self) -> None:
        """Locked status renders the call-to-action pill."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(locked=True, passphrase_source=None)
        app_class._render_status_pill(instance, status)
        bar.set_message.assert_called_once()
        assert "LOCKED" in bar.set_message.call_args[0][0]

    @staticmethod
    def _fake_status(*, acknowledged: bool, source: object) -> object:
        from terok.lib.integrations.sandbox import RecoveryStatus

        return RecoveryStatus(acknowledged=acknowledged, source=source)

    def test_render_pill_unlocked_shows_source(self, monkeypatch) -> None:
        """Resolved tier surfaces in the pill text (recovery key already acked)."""
        app_mod, app_class = import_app()
        monkeypatch.setattr(
            app_mod,
            "recovery_status",
            lambda: self._fake_status(acknowledged=True, source="keyring"),
        )
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(passphrase_source="keyring")
        app_class._render_status_pill(instance, status)
        bar.set_message.assert_called_once_with("Vault: unlocked (keyring)")

    def test_render_pill_unlocked_appends_unconfirmed_recovery(self, monkeypatch) -> None:
        """Missing recovery-ack marker on a durable tier → ``UNCONFIRMED`` suffix."""
        app_mod, app_class = import_app()
        monkeypatch.setattr(
            app_mod,
            "recovery_status",
            lambda: self._fake_status(acknowledged=False, source="systemd-creds"),
        )
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(passphrase_source="systemd-creds")
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "systemd-creds" in message
        assert "recovery key UNCONFIRMED" in message
        # The session-only escalation must not bleed into the durable branch.
        assert "vault dies on reboot" not in message

    def test_render_pill_session_only_escalates_pill_text(self, monkeypatch) -> None:
        """Missing marker + session-file source → louder pill text."""
        app_mod, app_class = import_app()
        monkeypatch.setattr(
            app_mod,
            "recovery_status",
            lambda: self._fake_status(acknowledged=False, source="session-file"),
        )
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(passphrase_source="session-file")
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "session-file" in message
        # The escalation explicitly names the reboot-loss risk so the
        # operator sees the asymmetry against durable tiers at a glance.
        assert "UNSAVED" in message
        assert "vault dies on reboot" in message

    def test_render_pill_unlocked_appends_plaintext_marker(self) -> None:
        """sandbox#282: pill flags plaintext-on-disk even when another tier unlocked."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(
            passphrase_source="systemd-creds",
            plaintext_passphrase_path=MOCK_BASE / "etc" / "terok" / "config.yml",
        )
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "systemd-creds" in message
        assert "plaintext" in message

    def test_render_pill_unknown_source_clears(self) -> None:
        """Unlocked but no resolved tier means the pill goes blank."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(locked=False, passphrase_source=None)
        app_class._render_status_pill(instance, status)
        bar.set_message.assert_called_once_with("")

    def test_render_pill_none_status_clears(self) -> None:
        """``None`` status (probe failed) clears the pill."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        app_class._render_status_pill(instance, None)
        bar.set_message.assert_called_once_with("")

    def test_render_pill_no_status_bar_yet_is_silent(self) -> None:
        """Pre-mount call (no StatusBar yet) is swallowed silently."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        instance.query_one = mock.Mock(side_effect=app_mod.NoMatches("not yet"))
        # Should NOT raise; method returns without touching anything.
        app_class._render_status_pill(instance, make_vault_status())


class TestRefreshVaultStatus:
    """[`_refresh_vault_status`][terok.tui.app.TerokTUI._refresh_vault_status] probe + modal trigger behaviour."""

    def _make_instance(self, app_class: type) -> object:
        instance = mock.Mock(spec=app_class)
        instance._render_status_pill = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        return instance

    def test_refresh_probes_and_stores_status(self) -> None:
        """Fresh probe lands in ``_last_vault_status`` and feeds the pill."""
        app_mod, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(locked=False, passphrase_source="keyring")
        with mock.patch.object(app_mod, "get_vault_status", return_value=status):
            run(app_class._refresh_vault_status(instance))
        assert instance._last_vault_status is status
        instance._render_status_pill.assert_called_once_with(status)
        instance.push_screen.assert_not_called()

    def test_refresh_probe_failure_clears_status(self) -> None:
        """``get_vault_status`` raising still updates the pill (with ``None``)."""
        app_mod, app_class = import_app()
        instance = self._make_instance(app_class)
        with mock.patch.object(app_mod, "get_vault_status", side_effect=RuntimeError("nope")):
            run(app_class._refresh_vault_status(instance, push_modal_if_locked=True))
        assert instance._last_vault_status is None
        instance._render_status_pill.assert_called_once_with(None)
        instance.push_screen.assert_not_called()

    def test_refresh_locked_pushes_modal_when_requested(self) -> None:
        """Locked vault + ``push_modal_if_locked=True`` opens the unlock modal."""
        app_mod, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(locked=True, passphrase_source=None)
        with mock.patch.object(app_mod, "get_vault_status", return_value=status):
            run(app_class._refresh_vault_status(instance, push_modal_if_locked=True))
        instance.push_screen.assert_awaited_once()
        modal_arg = instance.push_screen.call_args[0][0]
        assert isinstance(modal_arg, app_mod.VaultUnlockModal)
        # Callback is the unlock-result coroutine.
        assert instance.push_screen.call_args[0][1] == instance._on_vault_unlock_result

    def test_refresh_locked_without_request_skips_modal(self) -> None:
        """Even when locked, the modal stays closed if the caller didn't ask."""
        app_mod, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(locked=True, passphrase_source=None)
        with mock.patch.object(app_mod, "get_vault_status", return_value=status):
            run(app_class._refresh_vault_status(instance, push_modal_if_locked=False))
        instance.push_screen.assert_not_called()


class TestOnVaultUnlockResult:
    """[`_on_vault_unlock_result`][terok.tui.app.TerokTUI._on_vault_unlock_result] session-file write path."""

    def _make_instance(self, app_class: type, passphrase_file: object) -> object:
        instance = mock.Mock(spec=app_class)
        instance._refresh_vault_status = mock.AsyncMock()
        instance.notify = mock.Mock()
        cfg = mock.Mock()
        cfg.vault_passphrase_file = passphrase_file
        instance._test_sandbox_cfg = cfg
        return instance

    def test_empty_passphrase_is_a_noop(self, tmp_path: object) -> None:
        """An empty / ``None`` passphrase shortcuts before touching the disk."""
        app_mod, app_class = import_app()
        instance = self._make_instance(app_class, tmp_path / "session" / "passphrase")
        with mock.patch.object(app_mod, "SandboxConfig") as ctor:
            run(app_class._on_vault_unlock_result(instance, None))
            run(app_class._on_vault_unlock_result(instance, ""))
        ctor.assert_not_called()
        instance._refresh_vault_status.assert_not_awaited()

    def test_writes_session_file_and_reprobes(self, tmp_path: object) -> None:
        """Happy path: chmod 0o600, content ends with newline, pill re-rendered."""
        app_mod, app_class = import_app()
        target = tmp_path / "session" / "passphrase"
        instance = self._make_instance(app_class, target)
        with mock.patch.object(app_mod, "SandboxConfig", return_value=instance._test_sandbox_cfg):
            run(app_class._on_vault_unlock_result(instance, "hunter2"))
        assert target.read_text(encoding="utf-8") == "hunter2\n"
        # 0o600 — owner rw, nothing else.
        assert target.stat().st_mode & 0o777 == 0o600
        # Re-probe drives the pill refresh; modal stays closed.
        instance._refresh_vault_status.assert_awaited_once_with()
        # Friendly notify so the operator knows the write landed.
        instance.notify.assert_called_once()

    def test_oserror_surfaces_via_notify(self, tmp_path: object) -> None:
        """A disk failure shows an error notification and skips the re-probe."""
        app_mod, app_class = import_app()
        # Point the passphrase file at a path under a non-directory parent so
        # the ``parent.mkdir`` call raises ``NotADirectoryError`` (an OSError
        # subclass) without the test having to fabricate one.
        not_a_dir = tmp_path / "regular-file"
        not_a_dir.write_text("blocker")
        instance = self._make_instance(app_class, not_a_dir / "child" / "passphrase")
        with mock.patch.object(app_mod, "SandboxConfig", return_value=instance._test_sandbox_cfg):
            run(app_class._on_vault_unlock_result(instance, "swordfish"))
        instance._refresh_vault_status.assert_not_awaited()
        # Notify is called with the error severity — the operator sees a red toast.
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs.get("severity") == "error"
