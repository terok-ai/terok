# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI detail screens (Phase 2) and rendering helpers."""

import asyncio
import contextlib
import inspect
import sys
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

import pytest
import textual.theme  # noqa: F401 — prime the real module; see comment below
from rich.text import Text

from tests.testfs import MOCK_BASE, MOCK_CONFIG_ROOT
from tests.testnet import TEST_EGRESS_URL, TEST_UPSTREAM_URL
from tests.unit.tui.tui_test_helpers import (
    import_app,
    import_screens,
    import_widgets,
    make_key_event,
)

# ``terok.tui.app`` imports ``textual.theme`` at module level, and the
# stub map in ``tui_test_helpers`` does not cover that submodule (the
# stubbed ``textual`` is not a package, so the import can't fall through
# to disk).  The ``import textual.theme`` above primes the real module
# in ``sys.modules`` so the stubbed re-imports below resolve it there,
# regardless of collection order.

MOCK_WORKSPACE = str(MOCK_BASE / "ws")
TEST_PROJECT_NAME = "test-proj"
TEST_PROJECT_ROOT = MOCK_CONFIG_ROOT / "projects" / TEST_PROJECT_NAME


def make_project(**overrides: object) -> mock.Mock:
    """Return a project mock with sensible defaults for TUI rendering tests."""
    project = mock.Mock()
    project.name = TEST_PROJECT_NAME
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
    screen = screens.TaskDetailsScreen(task=task, has_tasks=has_tasks, project_name="p")
    screen.dismiss = mock.Mock()
    return screen


def render_task_details_text(**overrides: object) -> str:
    """Render task details and return plain text for substring assertions."""
    widgets = import_widgets()
    task = make_task(widgets, **overrides)
    return str(widgets.render_task_details(task, project_name="proj1"))


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
    instance.current_project_name = "proj1"
    instance._last_selected_tasks = {}
    instance.notify = mock.Mock()
    instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
    instance._save_selection_state = mock.Mock()
    instance.refresh_tasks = mock.AsyncMock()
    instance.push_screen = fake_push_screen
    instance._mark_launching = mock.Mock()
    instance.run_worker = mock.Mock()
    return instance


def _task_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.TASK_ACTION_HANDLERS.items())


def _auth_providers() -> list[str]:
    from terok_executor import AUTH_PROVIDERS

    return list(AUTH_PROVIDERS)


def _project_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.PROJECT_ACTION_HANDLERS.items())


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
        assert TEST_PROJECT_NAME in text_str

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

    def test_render_project_details_shows_description(self) -> None:
        widgets = import_widgets()
        project = make_project(description="A friendly summary")
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}

        result = widgets.render_project_details(project, state, task_count=5)

        assert "A friendly summary" in str(result)

    def test_render_project_details_omits_description_when_none(self) -> None:
        widgets = import_widgets()
        project = make_project(description=None)
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}

        result = widgets.render_project_details(project, state, task_count=5)

        assert "Desc:" not in str(result)

    def test_render_task_details_returns_text(self) -> None:
        widgets = import_widgets()

        task = make_task(widgets, task_id="42", backend="codex")

        result = widgets.render_task_details(task, project_name="proj1")

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
        project = make_project(name="myproj", upstream_url=TEST_EGRESS_URL)

        result = widgets.render_project_loading(project, task_count=3)

        assert isinstance(result, Text)
        text_str = str(result)
        assert "myproj" in text_str

    def test_render_project_loading_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_loading(None)

        assert isinstance(result, Text)
        assert "No project" in str(result)

    def test_render_project_loading_shows_description(self) -> None:
        widgets = import_widgets()
        project = make_project(description="Loading-time summary")

        result = widgets.render_project_loading(project, task_count=1)

        assert "Loading-time summary" in str(result)

    def test_render_project_loading_omits_description_when_none(self) -> None:
        widgets = import_widgets()
        project = make_project(description=None)

        result = widgets.render_project_loading(project, task_count=1)

        assert "Desc:" not in str(result)

    def test_render_task_details_unattended_mode(self) -> None:
        widgets = import_widgets()
        task = make_task(widgets, task_id="5", mode="run")
        result = widgets.render_task_details(task, project_name="proj1")
        assert isinstance(result, Text)
        text_str = str(result)
        assert "Unattended" in text_str
        assert "terok task logs" in text_str

    def test_render_task_details_unattended_with_exit_code(self) -> None:
        widgets = import_widgets()
        task = make_task(widgets, task_id="5", mode="run", exit_code=0)
        result = widgets.render_task_details(task, project_name="proj1")
        text_str = str(result)
        assert "Exit code: 0" in text_str

    def test_web_url_emits_osc8_link_outside_web_mode(self) -> None:
        """In a real terminal the URL style carries an OSC 8 link for cross-line wrap."""
        import io

        from rich.console import Console

        widgets = import_widgets()
        task = make_task(widgets, task_id="42", mode="toad", web_port=8123, web_token="t0k")
        result = widgets.render_task_details(task, project_name="proj1", is_web=False)
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
        result = widgets.render_task_details(task, project_name="proj1", is_web=True)
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
        text = str(widgets.render_task_details(task, project_name="proj1", shield_hooks_ok=True))
        assert "ready" in text
        assert "offline" not in text
        assert "shield-security" not in text

    def test_render_shield_offline_stopped_hooks_broken_shows_warning(self) -> None:
        """Stopped containers with broken hooks still show offline warning."""
        widgets = import_widgets()
        task = make_task(widgets, task_id="99", shield_state="OFFLINE", container_state="exited")
        text = str(widgets.render_task_details(task, project_name="proj1", shield_hooks_ok=False))
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
                id="unattended",
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
        project = make_project(name="proj1")
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
        project = make_project(name="proj1")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=0)
        screen._open_agents_modal = mock.Mock()
        screen.action_set_agents()
        screen._open_agents_modal.assert_called_once_with()

    def test_project_details_option_list_routes_set_agents(self) -> None:
        """Selecting the OptionList entry pushes the modal instead of dismissing."""
        screens, _ = import_screens()
        project = make_project(name="proj1")
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
        project = make_project(name="proj1", agents="all")
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
        project = make_project(name="proj1", agents="all")
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
            project_name="proj1",
            image_old=False,
        )
        assert screen._task_meta == task
        assert screen._has_tasks
        assert screen._project_name == "proj1"
        assert not screen._image_old

    @pytest.mark.parametrize("screen_name", ["AuthActionsScreen", "UnattendedPromptScreen"])
    def test_simple_screen_construction(self, screen_name: str) -> None:
        screens, _ = import_screens()
        assert getattr(screens, screen_name)() is not None

    def test_agent_selection_screen_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen()
        assert screen is not None
        assert screen._default_agent == "claude"

    def test_agent_selection_screen_custom_default(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        assert screen._default_agent == "codex"

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

    def test_agent_selection_screen_submit_returns_agent(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        screen.dismiss = mock.Mock()
        screen._submit()
        screen.dismiss.assert_called_once_with("codex")

    def test_agent_selection_screen_number_key_updates_selection(self) -> None:
        screens, _ = import_screens()
        from terok.lib.api.agents import AGENTS

        names = list(AGENTS)
        # Default to the last agent so pressing "1" (the first) always changes
        # the selection regardless of registry order.
        screen = screens.AgentSelectionScreen(default_agent=names[-1])
        # Stub query_one to return a mock OptionList
        mock_option_list = mock.Mock()
        screen.query_one = mock.Mock(return_value=mock_option_list)
        event = make_key_event("1")
        event.character = "1"
        screen.on_key(event)
        assert screen._selected_agent == names[0]
        event.stop.assert_called_once()


class TestTaskScreenKeyBinding:
    """Tests for TaskDetailsScreen.on_key case-sensitive dispatch."""

    @pytest.mark.parametrize(
        ("key", "has_tasks", "expected", "mode", "should_stop"),
        [
            pytest.param("c", False, "task_start_cli", None, True, id="lower-c"),
            pytest.param("w", False, "task_start_toad", None, True, id="lower-w"),
            pytest.param("U", False, "task_start_unattended", None, True, id="shift-u"),
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
            pytest.param("f", True, "follow_logs", "run", None, id="follow-unattended"),
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

    def test_auth_screen_lists_every_provider(self) -> None:
        """Every ``AUTH_PROVIDERS`` entry is listed — no 9-entry shortcut cap."""
        from terok_executor import AUTH_PROVIDERS

        screens, _ = import_screens()
        screen = screens.AuthActionsScreen()
        (option_list,) = list(screen.compose())
        ids = [opt._stub_kwargs.get("id") for opt in option_list._stub_args if opt is not None]
        assert ids == [f"auth_{name}" for name in AUTH_PROVIDERS] + ["import_opencode_config"]

    def test_auth_screen_badges_authenticated_entries(self) -> None:
        """Stored-credential entries get their option prompt badged."""
        from terok_executor import AUTH_PROVIDERS

        screens, _ = import_screens()
        screen = screens.AuthActionsScreen()
        option_list = mock.Mock()
        screen.query_one = mock.Mock(return_value=option_list)
        screen._show_auth_state(frozenset({"claude"}))
        option_list.replace_option_prompt.assert_called_once_with(
            "auth_claude", f"{AUTH_PROVIDERS['claude'].label}  ✓ authenticated"
        )

    def test_auth_screen_unreadable_vault_flags_subtitle(self) -> None:
        """``None`` (vault unreadable) flags the state instead of faking 'no auth'."""
        screens, _ = import_screens()
        screen = screens.AuthActionsScreen()
        dialog = mock.Mock()
        screen.query_one = mock.Mock(return_value=dialog)
        screen._show_auth_state(None)
        assert "vault locked" in dialog.border_subtitle

    def test_auth_screen_scopes_badge_query_to_project(self) -> None:
        """The modal's project scope drives the vault badge query."""
        screens, _ = import_screens()
        screen = screens.AuthActionsScreen("myproj")
        with mock.patch(
            "terok.lib.api.agents.authenticated_entries", return_value=frozenset()
        ) as mock_authed:
            screen._fetch_auth_state()
        mock_authed.assert_called_once_with("myproj")

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

    def test_action_run_unattended_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_run_unattended_from_main(instance))
        instance._action_task_start_unattended.assert_called_once()

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
        instance.current_project_name = "proj"
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
        instance.current_project_name = "proj"
        instance.push_screen = mock.AsyncMock()
        run(mixin._action_project_init(instance))
        instance.push_screen.assert_awaited_once()
        screen = instance.push_screen.call_args[0][0]
        assert type(screen).__name__ == "InitProgressScreen"
        assert screen._project_name == "proj"
        assert screen._rendered_yaml is None


class TestShowResolvedInstructions:
    """The instructions preview resolves with the project's package family."""

    def test_preview_threads_family_and_opens_viewer(self) -> None:
        from terok.tui.project_actions import ProjectActionsMixin

        instance = mock.Mock(spec=ProjectActionsMixin)
        instance.current_project_name = "proj"
        instance.push_screen = mock.AsyncMock()
        project = SimpleNamespace(
            agent_config={}, root=None, default_agent=None, known_family="rpm"
        )
        with (
            mock.patch("terok.tui.project_actions.load_project", return_value=project),
            mock.patch("terok.lib.api.resolve_agent_config", return_value={}),
            mock.patch(
                "terok.lib.api.agents.get_agent", return_value=SimpleNamespace(name="claude")
            ),
            mock.patch("terok.lib.api.agents.resolve_instructions", return_value="TEXT") as ri,
        ):
            run(ProjectActionsMixin._action_show_resolved_instructions(instance))
        assert ri.call_args == mock.call({}, "claude", project_root=None, family="rpm")
        instance.push_screen.assert_awaited_once()


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
        instance._run_suspended = mock.AsyncMock(return_value=0)
        instr_path = tmp_path / "nested" / "instructions.md"
        run(mixin._edit_in_external_editor(instance, instr_path, "vim -u NONE", done_msg="ok"))
        instance._run_suspended.assert_awaited_once_with(
            "vim", "-u", "NONE", str(instr_path), prompt_on_success=False
        )
        assert instr_path.parent.is_dir()
        instance.notify.assert_called_once_with("ok")
        instance._refresh_project_state.assert_called_once()

    def test_launch_failure_is_surfaced_not_raised(self, tmp_path: object) -> None:
        """A failed spawn (``None`` exit code) aborts quietly — no notify, no refresh."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instance._run_suspended = mock.AsyncMock(return_value=None)
        instr_path = tmp_path / "instructions.md"
        run(mixin._edit_in_external_editor(instance, instr_path, "bogus-editor", done_msg="ok"))
        instance.notify.assert_not_called()
        instance._refresh_project_state.assert_not_called()

    def test_nonzero_editor_exit_suppresses_success(self, tmp_path: object) -> None:
        """An editor exiting non-zero (e.g. vim ``:cq``) must not claim success."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instance._run_suspended = mock.AsyncMock(return_value=1)
        instr_path = tmp_path / "instructions.md"
        run(mixin._edit_in_external_editor(instance, instr_path, "vim", done_msg="ok"))
        instance.notify.assert_not_called()
        instance._refresh_project_state.assert_not_called()


class TestActionAuth:
    """Per-project ``_action_auth`` and host-wide ``_action_auth_host_wide`` dispatch."""

    def _get_mixin(self):
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_per_project_dispatches_auth_with_project_name(self) -> None:
        """``_action_auth`` is the project-details path — passes the selection."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_name = "myproj"
        # ``_run_auth_flow`` is ``@work``-decorated — sync at call time despite
        # the async source.  Override the autospec'd AsyncMock so the call
        # doesn't leak an unawaited coroutine.
        instance._run_auth_flow = mock.Mock()
        run(mixin._action_auth(instance, "claude"))
        instance._run_auth_flow.assert_called_once_with("claude", "myproj")

    def test_per_project_without_selection_notifies_and_skips(self) -> None:
        """Without a selection ``_action_auth`` is a no-op — host-wide path is separate."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_name = None
        # ``notify`` lives on the App parent, not the mixin spec — wire it
        # explicitly so the early-return path can call it without erroring.
        instance.notify = mock.Mock()
        instance._run_auth_flow = mock.Mock()

        run(mixin._action_auth(instance, "claude"))
        instance._run_auth_flow.assert_not_called()
        instance.notify.assert_called_once()

    def test_host_wide_dispatches_auth_with_none(self) -> None:
        """``_action_auth_host_wide`` ignores ``current_project_name`` by design."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_name = "selected-but-irrelevant"
        instance._run_auth_flow = mock.Mock()
        run(mixin._action_auth_host_wide(instance, "claude"))
        instance._run_auth_flow.assert_called_once_with("claude", None)


class TestAuthFlow:
    """``_run_auth_flow_body`` mode selection and the per-mode auth helpers.

    These exercise the pure dispatch logic with the Textual surface
    (``push_screen_wait``, ``notify``) and the executor calls mocked —
    the container launch + podman-watch paths are integration-only.
    """

    def _get_mixin(self):
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def _provider(
        self, *, oauth: bool, api_key: bool, device: bool = False, label: str = "Claude"
    ) -> SimpleNamespace:
        """A stand-in for an ``AuthProvider`` roster entry."""
        return SimpleNamespace(
            supports_oauth=oauth,
            supports_api_key=api_key,
            supports_device_auth=device,
            label=label,
        )

    def _instance(self, mixin) -> mock.Mock:
        """A mixin mock with the App-provided surface wired up."""
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock()
        instance._auth_via_api_key = mock.AsyncMock()
        instance._auth_via_oauth = mock.AsyncMock()
        return instance

    @contextlib.contextmanager
    def _roster(self, provider: str, info: SimpleNamespace, *, oauth_enabled: bool):
        """Patch the provider roster and the OAuth gate for *provider*.

        The roster is patched in both places that read it: the membership
        check in ``_run_auth_flow_body`` (via ``terok.lib.api``) and
        ``available_auth_modes`` (via ``terok.lib.domain.auth``).  At runtime
        these are the same object; the test pins both names to the fake.
        """
        with (
            mock.patch("terok.lib.api.AUTH_PROVIDERS", {provider: info}),
            mock.patch("terok.lib.domain.auth.AUTH_PROVIDERS", {provider: info}),
            mock.patch("terok.lib.core.config.is_oauth_enabled_for", return_value=oauth_enabled),
        ):
            yield

    # ---- _run_auth_flow_body: mode selection ----

    def test_unknown_provider_notifies_and_skips(self) -> None:
        """An unknown provider name is reported and dispatches nothing."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        with mock.patch("terok.lib.api.AUTH_PROVIDERS", {}):
            run(mixin._run_auth_flow_body(instance, "nope", None))
        instance.notify.assert_called_once()
        instance._auth_via_api_key.assert_not_awaited()
        instance._auth_via_oauth.assert_not_awaited()

    def test_both_modes_prompts_then_oauth(self) -> None:
        """Dual-mode provider: the mode screen's ``oauth`` pick routes to OAuth."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instance.push_screen_wait.return_value = "oauth"
        with self._roster("claude", self._provider(oauth=True, api_key=True), oauth_enabled=True):
            run(mixin._run_auth_flow_body(instance, "claude", "proj"))
        instance.push_screen_wait.assert_awaited_once()
        instance._auth_via_oauth.assert_awaited_once_with(
            "claude", project_name="proj", device_auth=False
        )
        instance._auth_via_api_key.assert_not_awaited()

    def test_both_modes_prompts_then_api_key(self) -> None:
        """Dual-mode provider: the mode screen's ``api_key`` pick routes to API key."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instance.push_screen_wait.return_value = "api_key"
        with self._roster("claude", self._provider(oauth=True, api_key=True), oauth_enabled=True):
            run(mixin._run_auth_flow_body(instance, "claude", None))
        instance._auth_via_api_key.assert_awaited_once_with("claude", project_name=None)
        instance._auth_via_oauth.assert_not_awaited()

    def test_device_code_pick_routes_to_oauth_with_flag(self) -> None:
        """Picking ``device_auth`` runs the OAuth path with ``device_auth=True``."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instance.push_screen_wait.return_value = "device_auth"
        info = self._provider(oauth=True, api_key=True, device=True, label="Codex")
        with self._roster("codex", info, oauth_enabled=True):
            run(mixin._run_auth_flow_body(instance, "codex", None))
        instance._auth_via_oauth.assert_awaited_once_with(
            "codex", project_name=None, device_auth=True
        )
        instance._auth_via_api_key.assert_not_awaited()

    def test_both_modes_cancel_dispatches_nothing(self) -> None:
        """Dismissing the mode screen (``None``) aborts without dispatching."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        instance.push_screen_wait.return_value = None
        with self._roster("claude", self._provider(oauth=True, api_key=True), oauth_enabled=True):
            run(mixin._run_auth_flow_body(instance, "claude", None))
        instance._auth_via_api_key.assert_not_awaited()
        instance._auth_via_oauth.assert_not_awaited()

    def test_oauth_only_skips_mode_screen(self) -> None:
        """An OAuth-only provider goes straight to OAuth — no mode prompt."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        with self._roster("claude", self._provider(oauth=True, api_key=False), oauth_enabled=True):
            run(mixin._run_auth_flow_body(instance, "claude", None))
        instance.push_screen_wait.assert_not_awaited()
        instance._auth_via_oauth.assert_awaited_once_with(
            "claude", project_name=None, device_auth=False
        )

    def test_api_key_only_skips_mode_screen(self) -> None:
        """An API-key-only provider goes straight to the key form — no mode prompt."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        info = self._provider(oauth=False, api_key=True, label="Blablador")
        with self._roster("blablador", info, oauth_enabled=True):
            run(mixin._run_auth_flow_body(instance, "blablador", None))
        instance.push_screen_wait.assert_not_awaited()
        instance._auth_via_api_key.assert_awaited_once_with("blablador", project_name=None)

    def test_oauth_gated_off_falls_back_to_api_key(self) -> None:
        """A dual-mode provider with the OAuth gate closed uses API key, no prompt."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        with self._roster("claude", self._provider(oauth=True, api_key=True), oauth_enabled=False):
            run(mixin._run_auth_flow_body(instance, "claude", None))
        instance.push_screen_wait.assert_not_awaited()
        instance._auth_via_api_key.assert_awaited_once_with("claude", project_name=None)

    def test_oauth_only_but_gated_off_errors(self) -> None:
        """OAuth-only provider with the gate closed has no usable mode — it errors."""
        mixin = self._get_mixin()
        instance = self._instance(mixin)
        with self._roster("claude", self._provider(oauth=True, api_key=False), oauth_enabled=False):
            run(mixin._run_auth_flow_body(instance, "claude", None))
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs.get("severity") == "error"
        instance._auth_via_oauth.assert_not_awaited()

    # ---- _auth_via_api_key ----

    def test_api_key_stores_and_notifies(self) -> None:
        """A submitted key is stored in the vault and confirmed."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value="sk-test-key")
        with mock.patch("terok.lib.api.store_api_key") as store:
            run(mixin._auth_via_api_key(instance, "claude", project_name=None))
        store.assert_called_once_with("claude", "sk-test-key", credential_set="default")
        instance.notify.assert_called_once()

    def test_api_key_empty_is_noop(self) -> None:
        """A cancelled / empty key form stores nothing."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value=None)
        with mock.patch("terok.lib.api.store_api_key") as store:
            run(mixin._auth_via_api_key(instance, "claude", project_name=None))
        store.assert_not_called()

    def test_api_key_store_failure_notifies_error(self) -> None:
        """A vault write failure surfaces as an error toast, not a crash."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen_wait = mock.AsyncMock(return_value="sk")
        with mock.patch("terok.lib.api.store_api_key", side_effect=RuntimeError("vault locked")):
            run(mixin._auth_via_api_key(instance, "claude", project_name=None))
        assert instance.notify.call_args.kwargs.get("severity") == "error"

    # ---- _auth_via_oauth ----

    def test_oauth_host_wide_missing_image_warns(self) -> None:
        """Host-wide OAuth with no L1 image present warns and never launches."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance._launch_oauth_container = mock.AsyncMock()
        with mock.patch("terok.lib.api.find_host_auth_image", return_value=None):
            run(mixin._auth_via_oauth(instance, "claude", project_name=None))
        assert instance.notify.call_args.kwargs.get("severity") == "warning"
        instance._launch_oauth_container.assert_not_awaited()

    def test_oauth_host_wide_prepares_and_launches(self) -> None:
        """Host-wide OAuth resolves the L1 image, prepares a session, launches it."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance._launch_oauth_container = mock.AsyncMock()
        session = mock.Mock()
        authenticator = mock.Mock()
        authenticator.prepare_oauth.return_value = session
        with (
            mock.patch("terok.lib.api.find_host_auth_image", return_value="terok-l1:test"),
            mock.patch("terok.lib.api.Authenticator", return_value=authenticator),
            mock.patch("terok.lib.core.config.sandbox_live_mounts_dir", return_value=MOCK_BASE),
        ):
            run(mixin._auth_via_oauth(instance, "claude", project_name=None))
        authenticator.prepare_oauth.assert_called_once()
        instance._launch_oauth_container.assert_awaited_once_with(session)

    def test_oauth_device_auth_forwarded_to_prepare(self) -> None:
        """``device_auth=True`` rides through to ``Authenticator.prepare_oauth``."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance._launch_oauth_container = mock.AsyncMock()
        authenticator = mock.Mock()
        authenticator.prepare_oauth.return_value = mock.Mock()
        with (
            mock.patch("terok.lib.api.find_host_auth_image", return_value="terok-l1:test"),
            mock.patch("terok.lib.api.Authenticator", return_value=authenticator),
            mock.patch("terok.lib.core.config.sandbox_live_mounts_dir", return_value=MOCK_BASE),
        ):
            run(mixin._auth_via_oauth(instance, "codex", project_name=None, device_auth=True))
        assert authenticator.prepare_oauth.call_args.kwargs["device_auth"] is True

    def test_oauth_project_scoped_uses_project_image(self) -> None:
        """Project-scoped OAuth reuses the project's CLI image, skips host resolution."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance._launch_oauth_container = mock.AsyncMock()
        session = mock.Mock()
        authenticator = mock.Mock()
        authenticator.prepare_oauth.return_value = session
        with (
            mock.patch("terok.lib.core.images.project_cli_image", return_value="proj:img") as pci,
            mock.patch("terok.lib.api.find_host_auth_image") as find_host,
            mock.patch("terok.lib.api.Authenticator", return_value=authenticator),
            mock.patch(
                "terok.lib.api.resolve_credential_routing", return_value=(MOCK_BASE, "myproj")
            ),
        ):
            run(mixin._auth_via_oauth(instance, "claude", project_name="myproj"))
        pci.assert_called_once_with("myproj")
        find_host.assert_not_called()
        # Per-project credential set reaches prepare_oauth.
        assert authenticator.prepare_oauth.call_args.kwargs["credential_set"] == "myproj"
        instance._launch_oauth_container.assert_awaited_once_with(session)

    # ---- _capture_auth_session ----

    def test_capture_success_stores_and_cleans_up(self) -> None:
        """A clean container exit captures credentials and tears the session down."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        session = mock.Mock()
        session.provider.name = "claude"
        mixin._capture_auth_session(instance, session, exit_code=0)
        session.capture.assert_called_once()
        session.cleanup.assert_called_once()
        instance.notify.assert_called_once()

    def test_capture_nonzero_exit_skips_capture(self) -> None:
        """A non-zero exit warns, skips capture, but still cleans up."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        session = mock.Mock()
        mixin._capture_auth_session(instance, session, exit_code=130)
        session.capture.assert_not_called()
        session.cleanup.assert_called_once()
        assert instance.notify.call_args.kwargs.get("severity") == "warning"

    def test_capture_extractor_error_still_cleans_up(self) -> None:
        """An extractor / vault error is reported and the session is still cleaned up."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        session = mock.Mock()
        session.provider.name = "claude"
        session.capture.side_effect = RuntimeError("extract failed")
        mixin._capture_auth_session(instance, session, exit_code=0)
        session.cleanup.assert_called_once()
        assert instance.notify.call_args.kwargs.get("severity") == "error"


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
        fake_project.default_shell = None
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

    def test_unattended_launch_selects_created_task(self) -> None:
        app_mod, app_class = import_app()

        instance = app_class()
        instance.current_project_name = "proj1"
        instance._last_selected_tasks = {}
        instance.notify = mock.Mock()
        instance._save_selection_state = mock.Mock()
        instance._start_unattended_watcher = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "unattended-launch"
        worker.result = ("proj1", "123", None)
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        assert instance._last_selected_tasks.get("proj1") == "123"
        instance._save_selection_state.assert_called_once()
        instance._start_unattended_watcher.assert_called_once_with("proj1", "123")
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
        instance.current_project_name = "proj1"
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
        instance.current_project_name = None
        instance.notify = mock.Mock()
        run(mixin._action_sync_gate(instance))
        instance._run_console_action.assert_not_called()
        instance.notify.assert_called_once()


class TestProjectScreenNoneState:
    """Tests that ProjectDetailsScreen handles None state correctly."""

    def test_project_screen_stores_none_state(self) -> None:
        screens, _ = import_screens()
        project = make_project(name="proj1")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=3)
        assert screen._state is None
        assert screen._task_count == 3


class TestCommandPalette:
    """Tests for command palette customization."""

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
        with mock.patch(
            "terok.lib.integrations.executor.ExecutorConfigView.set_image_agents"
        ) as write_mock:
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
        with mock.patch(
            "terok.lib.integrations.executor.ExecutorConfigView.set_image_agents"
        ) as write_mock:
            run(app_class._on_default_agents_result(instance, None))
        write_mock.assert_not_called()
        instance.notify.assert_not_called()


def _binding_key_action(b: object) -> tuple[str, str]:
    """Extract ``(key, action)`` from a plain-tuple or stub ``Binding`` entry.

    The stub ``Binding`` (a ``_StubObject``) isn't subscriptable; it stashes
    its constructor args on ``_stub_args``.  Plain tuples index directly.
    """
    if isinstance(b, tuple):
        return (b[0], b[1])
    return (b._stub_args[0], b._stub_args[1])  # type: ignore[attr-defined]


class TestVimNavigation:
    """Hidden hjkl bindings and the cursor/pane actions they drive."""

    def test_hjkl_bound_to_vim_actions_and_hidden(self) -> None:
        """j/k/h/l map to the vim_* actions and never surface in the footer."""
        _, app_class = import_app()
        by_key = {b._stub_args[0]: b for b in app_class.BINDINGS if not isinstance(b, tuple)}
        expected = {"j": "vim_down", "k": "vim_up", "h": "vim_left", "l": "vim_right"}
        for key, action in expected.items():
            binding = by_key[key]
            assert binding._stub_args[1] == action
            assert binding._stub_kwargs.get("show") is False, f"{key} must be hidden"

    def test_vim_move_cursor_drives_focused_widget(self) -> None:
        """j/k forward to the focused widget's cursor action when it has one."""
        _, app_class = import_app()
        instance = mock.Mock()
        instance.focused = mock.Mock(spec=["action_cursor_down"])
        app_class._vim_move_cursor(instance, "action_cursor_down")
        instance.focused.action_cursor_down.assert_called_once_with()

    def test_vim_move_cursor_noop_without_cursor_action(self) -> None:
        """A focused widget lacking the action (e.g. a Button) is left untouched."""
        _, app_class = import_app()
        instance = mock.Mock()
        instance.focused = object()  # no ``action_cursor_down`` attribute
        app_class._vim_move_cursor(instance, "action_cursor_down")  # must not raise

    def test_vim_focus_pane_switches_between_main_lists(self) -> None:
        """h/l move focus to the other pane when a main pane already holds it."""
        app_mod, app_class = import_app()
        instance = mock.Mock()
        instance.focused = mock.Mock(spec=app_mod.ProjectList)  # left pane focused
        target = mock.Mock()
        instance.query_one.return_value = target
        app_class._vim_focus_pane(instance, app_mod.TaskList)
        instance.query_one.assert_called_once_with(app_mod.TaskList)
        target.focus.assert_called_once_with()

    def test_vim_focus_pane_inert_inside_modal(self) -> None:
        """With a non-pane widget focused (a modal menu), h/l do nothing."""
        app_mod, app_class = import_app()
        instance = mock.Mock()
        instance.focused = object()  # not a ProjectList / TaskList
        app_class._vim_focus_pane(instance, app_mod.TaskList)
        instance.query_one.assert_not_called()

    def test_action_vim_down_forwards_cursor_down(self) -> None:
        """The ``j`` action asks the focused widget to move its cursor down."""
        _, app_class = import_app()
        instance = mock.Mock()
        app_class.action_vim_down(instance)
        instance._vim_move_cursor.assert_called_once_with("action_cursor_down")

    def test_action_vim_up_forwards_cursor_up(self) -> None:
        """The ``k`` action asks the focused widget to move its cursor up."""
        _, app_class = import_app()
        instance = mock.Mock()
        app_class.action_vim_up(instance)
        instance._vim_move_cursor.assert_called_once_with("action_cursor_up")

    def test_action_vim_left_targets_the_projects_pane(self) -> None:
        """The ``h`` action routes focus toward the left (projects) pane."""
        app_mod, app_class = import_app()
        instance = mock.Mock()
        app_class.action_vim_left(instance)
        instance._vim_focus_pane.assert_called_once_with(app_mod.ProjectList)

    def test_action_vim_right_targets_the_tasks_pane(self) -> None:
        """The ``l`` action routes focus toward the right (tasks) pane."""
        app_mod, app_class = import_app()
        instance = mock.Mock()
        app_class.action_vim_right(instance)
        instance._vim_focus_pane.assert_called_once_with(app_mod.TaskList)


class TestGlobalAuthBinding:
    """The top-level ``a`` shortcut + ``action_authenticate`` route."""

    def test_app_binds_a_to_authenticate(self) -> None:
        """``a`` on the main screen opens the auth modal — no project required."""
        _, app_class = import_app()
        bindings = {_binding_key_action(b) for b in app_class.BINDINGS}
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


class TestProjectDetailsGateLine:
    """The project-details gate line reflects mirror existence + staleness.

    The gate now runs inside each task container's supervisor — there is
    no host gate daemon whose status could override the mirror state.
    """

    def test_gate_line_yes_when_mirror_present(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}

        result = widgets.render_project_details(project, state, task_count=5)
        text_str = str(result)
        assert "gate down" not in text_str
        assert "yes" in text_str

    def test_gate_line_no_when_mirror_absent(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": False}

        result = widgets.render_project_details(project, state, task_count=5)
        text_str = str(result)
        assert "gate down" not in text_str


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
                kwargs.get("project_name", "proj1"),
                kwargs.get("task_id", "3"),
                kwargs.get("task_name", "fix-login"),
            )
        finally:
            fn_globals["task_delete"] = orig

    def test_delete_task_success_returns_five_tuple(self) -> None:
        """Successful deletion returns (project_name, task_id, task_name, None, [])."""
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

MOCK_VAULT_DB = MOCK_BASE / "vault" / "credentials.db"


def make_vault_status(
    *,
    state: object | None = None,
    source: str | None = "keyring",
    providers: tuple[str, ...] | None = ("claude", "gh"),
    credential_types: dict[str, str] | None = None,
    ssh_keys: int | None = 0,
    db_path: object = MOCK_VAULT_DB,
    db_exists: bool = True,
    warnings: tuple[object, ...] = (),
    db_error: str | None = None,
    lock_reason: str | None = None,
) -> mock.Mock:
    """Build a vault status snapshot mock.

    Mirrors the sandbox-owned [`VaultStatus`][terok_sandbox.VaultStatus]
    shape — state classification, DB-side facts, and the shared warning
    catalog.  ``state`` defaults to ``UNLOCKED``.
    """
    from terok.lib.api.vault import VaultState

    status = mock.Mock()
    status.state = VaultState.UNLOCKED if state is None else state
    status.source = source
    status.providers = providers
    status.credential_types = credential_types or {}
    status.ssh_keys = ssh_keys
    status.db_path = db_path
    status.db_exists = db_exists
    status.warnings = warnings
    status.db_error = db_error
    status.lock_reason = lock_reason
    return status


def make_vault_warning(
    *,
    kind: str = "recovery-unconfirmed",
    severity: str = "warning",
    brief: str = "recovery key UNCONFIRMED",
    message: str = "the vault passphrase is not confirmed saved off-host",
) -> object:
    """Build a real ``VaultWarning`` catalog entry for status fakes."""
    from terok.lib.api.vault import VaultWarning, VaultWarningKind

    return VaultWarning(
        kind=VaultWarningKind(kind), severity=severity, brief=brief, message=message
    )


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
            pytest.param("action_vault_unlock", "vault_unlock", id="unlock"),
            pytest.param("action_vault_lock", "vault_lock", id="lock"),
            pytest.param("action_vault_seal", "vault_seal", id="seal"),
            pytest.param("action_vault_to_keyring", "vault_to_keyring", id="to-keyring"),
            pytest.param("action_vault_change", "vault_change", id="change"),
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

    def test_render_vault_status_unlocked_with_creds(self) -> None:
        """Unlocked snapshot shows credential names and the resolved passphrase tier."""
        screens, _ = import_screens()
        status = make_vault_status()
        text_str = str(screens.render_vault_status(status))
        assert "claude" in text_str
        assert "State:" in text_str
        assert "unlocked" in text_str
        assert "resolved via keyring" in text_str

    def test_render_vault_status_locked_shows_help_block(self) -> None:
        """Locked snapshot ends with the supervisor-aware unlock-hint block."""
        from terok.lib.api.vault import VaultState

        screens, _ = import_screens()
        status = make_vault_status(state=VaultState.LOCKED, source=None, providers=None)
        text_str = str(screens.render_vault_status(status))
        assert "Unlock" in text_str or "unlock" in text_str
        assert "supervisor" in text_str

    def test_render_vault_status_no_credentials(self) -> None:
        """Empty providers tuple renders 'none stored'."""
        screens, _ = import_screens()
        status = make_vault_status(providers=())
        result = screens.render_vault_status(status)
        assert "none stored" in str(result)

    def test_render_vault_status_shows_passphrase_source(self) -> None:
        """Resolved tier surfaces as ``Passphrase: resolved via <source>``."""
        screens, _ = import_screens()
        status = make_vault_status(source="systemd-creds", ssh_keys=3)
        text_str = str(screens.render_vault_status(status))
        assert "resolved via systemd-creds" in text_str
        assert "SSH keys:    3" in text_str

    def test_render_vault_status_announces_locked(self) -> None:
        """Locked vault prints an explicit ``State: LOCKED`` line with the no-tier reason."""
        from terok.lib.api.vault import VaultState

        screens, _ = import_screens()
        status = make_vault_status(state=VaultState.LOCKED, source=None, providers=None)
        text_str = str(screens.render_vault_status(status))
        assert "State:" in text_str
        assert "LOCKED" in text_str
        assert "no tier resolved" in text_str

    def test_render_vault_status_names_the_lock_reason(self) -> None:
        """A classified lock renders its reason instead of the generic fallback."""
        from terok.lib.api.vault import VaultState

        screens, _ = import_screens()
        status = make_vault_status(
            state=VaultState.LOCKED,
            source="session-file",
            providers=None,
            lock_reason="the passphrase via session-file does not open the DB",
        )
        text_str = str(screens.render_vault_status(status))
        assert "via session-file does not open the DB" in text_str
        assert "no tier resolved" not in text_str
        # And when locked, the Passphrase: line is suppressed (no tier to name).
        assert "resolved via" not in text_str

    def test_render_vault_status_marks_unlocked_explicitly(self) -> None:
        """Resolved vault shows ``State: unlocked`` plus which tier did it."""
        screens, _ = import_screens()
        status = make_vault_status(source="keyring")
        text_str = str(screens.render_vault_status(status))
        assert "State:       unlocked" in text_str
        assert "resolved via keyring" in text_str

    def test_render_vault_status_unprovisioned_points_at_setup(self) -> None:
        """A fresh install names the remedy — setup, not an unlock prompt."""
        from terok.lib.api.vault import VaultState

        screens, _ = import_screens()
        status = make_vault_status(
            state=VaultState.UNPROVISIONED, source=None, providers=None, db_exists=False
        )
        text_str = str(screens.render_vault_status(status))
        assert "not set up yet" in text_str
        assert "terok setup" in text_str

    def test_render_vault_status_renders_warning_catalog(self) -> None:
        """Catalog entries render as ``WARNING:`` lines (``note:`` for info severity)."""
        screens, _ = import_screens()
        status = make_vault_status(
            warnings=(
                make_vault_warning(
                    kind="recovery-unconfirmed",
                    severity="warning",
                    message="the vault passphrase is not confirmed saved off-host",
                ),
                make_vault_warning(
                    kind="shadow-redundant",
                    severity="info",
                    brief="redundant session file",
                    message="the session-file tier duplicates the durable keyring tier",
                ),
            )
        )
        text_str = str(screens.render_vault_status(status))
        assert "WARNING: the vault passphrase is not confirmed saved off-host" in text_str
        assert "note:    the session-file tier duplicates the durable keyring tier" in text_str

    def test_render_vault_status_shows_db_path(self) -> None:
        """The DB path (display-only) surfaces on every render.

        Mirrors the executor CLI's ``vault status`` output so the TUI
        and shell views agree at a glance.  Per-container daemon
        endpoints (broker / signer / socket) live inside each
        supervisor and are not rendered host-side anymore.
        """
        screens, _ = import_screens()
        status = make_vault_status()
        text_str = str(screens.render_vault_status(status))
        assert str(MOCK_VAULT_DB) in text_str
        # The DB exists — no fresh-install annotation.
        assert "created encrypted on first use" not in text_str

    def test_render_vault_status_annotates_missing_db(self) -> None:
        """``db_exists=False`` gains the created-on-first-use annotation."""
        screens, _ = import_screens()
        status = make_vault_status(db_exists=False)
        text_str = str(screens.render_vault_status(status))
        assert "created encrypted on first use" in text_str


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
        """_refresh_status fetches a new snapshot from the api facade."""
        from terok.lib.api.vault import VaultState

        screens, _ = import_screens()
        screen = screens.VaultScreen(make_vault_status(state=VaultState.LOCKED, source=None))
        detail = mock.Mock()
        screen.query_one = mock.Mock(return_value=detail)
        new_status = make_vault_status()
        # ``load_vault_status`` is bound by value at (re-)import time, so
        # patch the freshly imported screens module's own global.
        with mock.patch.object(screens, "load_vault_status", return_value=new_status):
            screen._refresh_status()
        assert screen._status is new_status
        detail.update.assert_called_once()

    def test_refresh_status_handles_exception(self) -> None:
        """_refresh_status sets status to None on failure."""
        screens, _ = import_screens()
        screen = screens.VaultScreen(make_vault_status())
        detail = mock.Mock()
        screen.query_one = mock.Mock(return_value=detail)
        with mock.patch.object(screens, "load_vault_status", side_effect=RuntimeError):
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
            ("vault_unlock", "_action_vault_unlock"),
            ("vault_lock", "_action_vault_lock"),
            ("vault_seal", "_action_vault_seal"),
            ("vault_to_keyring", "_action_vault_to_keyring"),
            ("vault_reveal", "_action_vault_reveal"),
            ("vault_acknowledge", "_action_vault_acknowledge"),
            ("vault_change", "_action_vault_change"),
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
        instance._action_vault_unlock.assert_not_called()
        instance._action_vault_lock.assert_not_called()
        instance._action_vault_seal.assert_not_called()
        instance._action_vault_to_keyring.assert_not_called()
        instance._action_vault_reveal.assert_not_called()
        instance._action_vault_acknowledge.assert_not_called()
        instance._action_vault_change.assert_not_called()


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
        """``_action_vault_unlock`` opens VaultUnlockModal wired to ``_on_vault_unlock_result``.

        The probe must classify the vault as LOCKED — on UNPROVISIONED
        the action routes to the first-passphrase provisioning flow
        instead of the unlock modal.
        """
        from terok.lib.api.vault import VaultState

        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.push_screen = mock.AsyncMock()
        # ``_on_vault_unlock_result`` lives on the composed App, not on the
        # mixin's TYPE_CHECKING stubs the spec sees — wire it explicitly so
        # we can compare against the value passed as the callback.
        instance._on_vault_unlock_result = mock.AsyncMock()
        status = make_vault_status(state=VaultState.LOCKED, source=None)
        with mock.patch("terok.lib.api.vault.load_vault_status", return_value=status):
            run(mixin._action_vault_unlock(instance))
        instance.push_screen.assert_awaited_once()
        modal_arg, callback_arg = instance.push_screen.call_args[0]
        # The handler imports VaultUnlockModal from the real ``terok.tui.screens``
        # module, distinct from the textual-stubbed copy ``import_screens``
        # produces — compare by class name rather than identity.
        assert type(modal_arg).__name__ == "VaultUnlockModal"
        assert callback_arg is instance._on_vault_unlock_result

    def test_lock_pushes_confirm_modal(self) -> None:
        """``_action_vault_lock`` gates the destructive clear behind a confirmation modal."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.push_screen = mock.AsyncMock()
        instance._on_vault_lock_confirmed = mock.Mock()
        run(mixin._action_vault_lock(instance))
        instance.push_screen.assert_awaited_once()
        modal_arg, callback_arg = instance.push_screen.call_args[0]
        assert type(modal_arg).__name__ == "ConfirmDestructiveScreen"
        assert callback_arg is instance._on_vault_lock_confirmed

    def test_lock_confirmed_dispatches_worker(self) -> None:
        """Confirming the modal runs the vault_lock worker + refreshes status."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        mixin._on_vault_lock_confirmed(instance, True)
        instance._run_console_action.assert_called_once_with(
            "terok.tui.worker_actions:vault_lock",
            title="Locking vault (clearing every stored copy)",
            refresh="vault_status",
        )

    def test_lock_cancelled_does_nothing(self) -> None:
        """Declining (or dismissing) the modal must not touch the vault."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        mixin._on_vault_lock_confirmed(instance, False)
        instance._run_console_action.assert_not_called()

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

    @staticmethod
    def _reveal_stubs(
        *,
        cfg: object,
        no_pass: type[Exception] = Exception,
        wrong_pass: type[Exception] = Exception,
        is_recovery_acknowledged: object = None,
    ) -> dict[str, mock.Mock]:
        """Stubs for the three api sub-modules ``_action_vault_reveal`` imports from.

        The action imports ``make_sandbox_config`` from ``terok.lib.api``,
        [`RecoveryStatus`][terok_sandbox.RecoveryStatus] from
        ``terok.lib.api.shield`` (and reads its
        ``is_acknowledged`` classmethod), and the two passphrase
        errors from ``terok.lib.api.vault`` — each gets its own
        ``sys.modules`` stub so the function-local imports resolve to
        the test mocks.
        """
        ack = is_recovery_acknowledged or mock.Mock(return_value=False)
        return {
            "terok.lib.api": mock.Mock(make_sandbox_config=lambda: cfg),
            "terok.lib.api.shield": mock.Mock(RecoveryStatus=mock.Mock(is_acknowledged=ack)),
            "terok.lib.api.vault": mock.Mock(
                NoPassphraseError=no_pass,
                WrongPassphraseError=wrong_pass,
            ),
        }

    def test_locked_vault_notifies_and_skips_modal(self) -> None:
        """No resolvable passphrase → notify, no modal."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.notify = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        stubs = self._reveal_stubs(cfg=self._make_cfg(passphrase=None))
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
        stubs = self._reveal_stubs(cfg=cfg, no_pass=_Wrong, wrong_pass=_Wrong)
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
        stubs = self._reveal_stubs(cfg=self._make_cfg())
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
        stubs = self._reveal_stubs(
            cfg=self._make_cfg(),
            is_recovery_acknowledged=mock.Mock(return_value=True),
        )
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
            "terok.lib.api": mock.Mock(make_sandbox_config=lambda: mock.Mock()),
            "terok.lib.api.shield": mock.Mock(RecoveryStatus=mock.Mock(acknowledge=ack_recovery)),
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
            "terok.lib.api": mock.Mock(make_sandbox_config=lambda: mock.Mock()),
            "terok.lib.api.shield": mock.Mock(RecoveryStatus=mock.Mock(acknowledge=ack_recovery)),
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
            "terok.lib.api": mock.Mock(make_sandbox_config=lambda: mock.Mock()),
            "terok.lib.api.shield": mock.Mock(RecoveryStatus=mock.Mock(acknowledge=ack_recovery)),
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
            "terok.lib.api": mock.Mock(make_sandbox_config=lambda: mock.Mock()),
            "terok.lib.api.shield": mock.Mock(RecoveryStatus=mock.Mock(acknowledge=ack_recovery)),
        }
        with mock.patch.dict(sys.modules, stubs):
            run(mixin._action_vault_acknowledge(instance))
        ack_recovery.assert_called_once()
        instance.notify.assert_called_once()
        assert "marked as saved" in instance.notify.call_args[0][0]
        instance._refresh_vault_status.assert_awaited_once()


class TestMaybeWarnRecoveryUnconfirmed:
    """One-shot startup notification for an unconfirmed recovery key.

    Three severity bands — silent / yellow warning / red error — read
    straight from the snapshot's shared warning catalog so the message
    escalates when the operator is one reboot away from losing the
    vault.
    """

    @staticmethod
    def _unconfirmed_warning() -> object:
        """The durable-tier catalog entry (``warning`` severity)."""
        return make_vault_warning(
            kind="recovery-unconfirmed",
            severity="warning",
            brief="recovery key UNCONFIRMED",
            message="the vault passphrase is not confirmed saved off-host",
        )

    @staticmethod
    def _volatile_warning() -> object:
        """The session-only catalog entry (``error`` severity)."""
        return make_vault_warning(
            kind="recovery-volatile",
            severity="error",
            brief="recovery key UNSAVED, vault dies on reboot",
            message=(
                "the only copy of the vault passphrase is the session file, which is"
                " cleared on reboot — save it off-host now or the vault becomes"
                " unrecoverable the next time this machine restarts"
            ),
        )

    def _make_instance(self, app_class: type, status: object) -> mock.Mock:
        instance = mock.Mock(spec=app_class)
        instance.notify = mock.Mock()
        instance._last_vault_status = status
        if hasattr(instance, "_recovery_warning_shown"):
            del instance._recovery_warning_shown
        return instance

    def test_warns_once_when_unlocked_and_unacked_durable(self) -> None:
        """Fresh process + unlocked vault + a warning-severity catalog entry → ``warning``."""
        _, app_class = import_app()
        instance = self._make_instance(
            app_class,
            make_vault_status(source="keyring", warnings=(self._unconfirmed_warning(),)),
        )
        app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs["severity"] == "warning"
        assert instance.notify.call_args.kwargs["title"] == "Vault: recovery key UNCONFIRMED"

    def test_errors_when_session_only(self) -> None:
        """The RECOVERY_VOLATILE entry → red ``error`` (one reboot away from loss)."""
        _, app_class = import_app()
        instance = self._make_instance(
            app_class,
            make_vault_status(source="session-file", warnings=(self._volatile_warning(),)),
        )
        app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs["severity"] == "error"
        body = instance.notify.call_args[0][0]
        assert "unrecoverable" in body.lower()
        assert "reboot" in body.lower()

    def test_quiet_when_no_recovery_warning(self) -> None:
        """Marker already landed → no recovery entry in the catalog → silent."""
        _, app_class = import_app()
        instance = self._make_instance(app_class, make_vault_status(source="keyring", warnings=()))
        app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_not_called()

    def test_quiet_when_locked(self) -> None:
        """Locked vault → unlock modal already pulls attention; suppress warning."""
        from terok.lib.api.vault import VaultState

        _, app_class = import_app()
        instance = self._make_instance(
            app_class,
            make_vault_status(
                state=VaultState.LOCKED,
                source=None,
                warnings=(self._unconfirmed_warning(),),
            ),
        )
        app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_not_called()

    def test_fires_at_most_once_per_session(self) -> None:
        """Second invocation does nothing — the flag survives the call."""
        _, app_class = import_app()
        instance = self._make_instance(
            app_class,
            make_vault_status(source="keyring", warnings=(self._unconfirmed_warning(),)),
        )
        app_class._maybe_warn_recovery_unconfirmed(instance)
        app_class._maybe_warn_recovery_unconfirmed(instance)
        instance.notify.assert_called_once()

    def test_quiet_when_status_is_none(self) -> None:
        """No probe yet (status=None) → nothing to warn about."""
        _, app_class = import_app()
        instance = self._make_instance(app_class, None)
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
        instance._ensure_credentials_provisioned = mock.AsyncMock(return_value=True)
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
        instance._ensure_credentials_provisioned = mock.AsyncMock(return_value=True)
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
        instance._ensure_credentials_provisioned = mock.AsyncMock(return_value=True)
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
        from terok.lib.api.vault import VaultState

        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(state=VaultState.LOCKED, source=None)
        app_class._render_status_pill(instance, status)
        bar.set_message.assert_called_once()
        assert "LOCKED" in bar.set_message.call_args[0][0]

    def test_render_pill_unlocked_shows_source(self) -> None:
        """Resolved tier surfaces in the pill text (no warnings pending)."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(source="keyring", warnings=())
        app_class._render_status_pill(instance, status)
        bar.set_message.assert_called_once_with("Vault: unlocked (keyring)")

    def test_render_pill_unlocked_appends_unconfirmed_recovery(self) -> None:
        """A warning-severity catalog entry rides the pill as a suffix."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(
            source="systemd-creds",
            warnings=(
                make_vault_warning(
                    kind="recovery-unconfirmed",
                    severity="warning",
                    brief="recovery key UNCONFIRMED",
                ),
            ),
        )
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "systemd-creds" in message
        assert "recovery key UNCONFIRMED" in message
        # The session-only escalation must not bleed into the durable branch.
        assert "vault dies on reboot" not in message

    def test_render_pill_session_only_escalates_pill_text(self) -> None:
        """The RECOVERY_VOLATILE brief → louder pill text."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(
            source="session-file",
            warnings=(
                make_vault_warning(
                    kind="recovery-volatile",
                    severity="error",
                    brief="recovery key UNSAVED, vault dies on reboot",
                ),
            ),
        )
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "session-file" in message
        # The escalation explicitly names the reboot-loss risk so the
        # operator sees the asymmetry against durable tiers at a glance.
        assert "UNSAVED" in message
        assert "vault dies on reboot" in message

    def test_render_pill_info_warning_stays_quiet(self) -> None:
        """Info-severity catalog entries (e.g. redundant shadow) never suffix the pill."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(
            source="keyring",
            warnings=(
                make_vault_warning(
                    kind="shadow-redundant",
                    severity="info",
                    brief="redundant session file",
                ),
            ),
        )
        app_class._render_status_pill(instance, status)
        bar.set_message.assert_called_once_with("Vault: unlocked (keyring)")

    def test_render_pill_unprovisioned_points_at_setup(self) -> None:
        """A fresh install renders the setup pointer, not an unlock nag."""
        from terok.lib.api.vault import VaultState

        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(state=VaultState.UNPROVISIONED, source=None)
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "not set up yet" in message
        assert "terok setup" in message

    def test_render_pill_error_carries_db_error(self) -> None:
        """A non-passphrase DB failure surfaces verbatim in the pill."""
        from terok.lib.api.vault import VaultState

        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        bar = mock.Mock()
        instance.query_one = mock.Mock(return_value=bar)
        status = make_vault_status(state=VaultState.ERROR, db_error="schema drift")
        app_class._render_status_pill(instance, status)
        message = bar.set_message.call_args[0][0]
        assert "ERROR" in message
        assert "schema drift" in message

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
        _, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(source="keyring")
        with mock.patch("terok.lib.api.vault.load_vault_status", return_value=status):
            run(app_class._refresh_vault_status(instance))
        assert instance._last_vault_status is status
        instance._render_status_pill.assert_called_once_with(status)
        instance.push_screen.assert_not_called()

    def test_refresh_probe_failure_clears_status(self) -> None:
        """``load_vault_status`` raising still updates the pill (with ``None``)."""
        _, app_class = import_app()
        instance = self._make_instance(app_class)
        with mock.patch("terok.lib.api.vault.load_vault_status", side_effect=RuntimeError("nope")):
            run(app_class._refresh_vault_status(instance, push_modal_if_locked=True))
        assert instance._last_vault_status is None
        instance._render_status_pill.assert_called_once_with(None)
        instance.push_screen.assert_not_called()

    def test_refresh_locked_pushes_modal_when_requested(self) -> None:
        """LOCKED + ``push_modal_if_locked=True`` opens the unlock modal."""
        from terok.lib.api.vault import VaultState

        app_mod, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(state=VaultState.LOCKED, source=None)
        with mock.patch("terok.lib.api.vault.load_vault_status", return_value=status):
            run(app_class._refresh_vault_status(instance, push_modal_if_locked=True))
        instance.push_screen.assert_awaited_once()
        modal_arg = instance.push_screen.call_args[0][0]
        assert isinstance(modal_arg, app_mod.VaultUnlockModal)
        # Callback is the unlock-result coroutine.
        assert instance.push_screen.call_args[0][1] == instance._on_vault_unlock_result

    def test_refresh_unprovisioned_never_pushes_modal(self) -> None:
        """UNPROVISIONED has nothing to unlock — the modal stays closed even when asked.

        First-time provisioning belongs to the chooser + create +
        reveal flow; a free-text modal here would silently key the
        future vault to whatever was typed.
        """
        from terok.lib.api.vault import VaultState

        _, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(state=VaultState.UNPROVISIONED, source=None)
        with mock.patch("terok.lib.api.vault.load_vault_status", return_value=status):
            run(app_class._refresh_vault_status(instance, push_modal_if_locked=True))
        instance.push_screen.assert_not_called()

    def test_refresh_locked_without_request_skips_modal(self) -> None:
        """Even when locked, the modal stays closed if the caller didn't ask."""
        from terok.lib.api.vault import VaultState

        _, app_class = import_app()
        instance = self._make_instance(app_class)
        status = make_vault_status(state=VaultState.LOCKED, source=None)
        with mock.patch("terok.lib.api.vault.load_vault_status", return_value=status):
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
        # Present NO durable tier and NO DB so the real (guarded, validating)
        # provision_session_passphrase neither refuses-as-shadow nor opens a
        # DB — it just writes, which is the path these tests exercise.  Real
        # absent paths, not Mock attrs, so ``.is_file()`` / ``.exists()`` are
        # honest ``False`` rather than truthy Mocks.
        cfg.vault_systemd_creds_file = passphrase_file.parent / "absent.cred"
        cfg.db_path = passphrase_file.parent / "absent.db"
        cfg.credentials_use_keyring = False
        cfg.credentials_passphrase = None
        cfg.credentials_passphrase_command = None
        instance._test_sandbox_cfg = cfg
        return instance

    def test_empty_passphrase_is_a_noop(self, tmp_path: object) -> None:
        """An empty / ``None`` passphrase shortcuts before touching the disk."""
        app_mod, app_class = import_app()
        instance = self._make_instance(app_class, tmp_path / "session" / "passphrase")
        with mock.patch("terok.lib.core.config.make_sandbox_config") as ctor:
            run(app_class._on_vault_unlock_result(instance, None))
            run(app_class._on_vault_unlock_result(instance, ""))
        ctor.assert_not_called()
        instance._refresh_vault_status.assert_not_awaited()

    def test_writes_session_file_and_reprobes(self, tmp_path: object) -> None:
        """Happy path: chmod 0o600, content ends with newline, pill re-rendered."""
        app_mod, app_class = import_app()
        target = tmp_path / "session" / "passphrase"
        instance = self._make_instance(app_class, target)
        with mock.patch(
            "terok.lib.core.config.make_sandbox_config",
            return_value=instance._test_sandbox_cfg,
        ):
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
        with mock.patch(
            "terok.lib.core.config.make_sandbox_config",
            return_value=instance._test_sandbox_cfg,
        ):
            run(app_class._on_vault_unlock_result(instance, "swordfish"))
        instance._refresh_vault_status.assert_not_awaited()
        # Notify is called with the error severity — the operator sees a red toast.
        instance.notify.assert_called_once()
        assert instance.notify.call_args.kwargs.get("severity") == "error"
