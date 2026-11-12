# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Keep launch warnings visible after initialization and task summaries."""

import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import pytest

from terok.lib.core.projects import load_project
from terok.lib.orchestration.task_runners import cli, headless, restart, toad
from terok.lib.orchestration.task_runners.container import _repeat_startup_warnings
from terok.lib.orchestration.tasks import load_task_meta, task_new
from tests.test_utils import mock_git_config, project_env
from tests.testnet import FAKE_PEER_PORT

_WARNINGS = (
    "WARNING: Wayland adds host attack surface.",
    "WARNING: Wayland is unavailable; the container continues.",
)
_PROJECT = "startup-warnings"


@pytest.mark.parametrize("mode", ["wl-direct", "wl-mitm"])
@pytest.mark.parametrize("tty,no_color", [(True, False), (False, False), (True, True)])
@pytest.mark.parametrize("detail", ["significantly increases host attack surface.", "unavailable."])
def test_only_direct_warning_marker_is_red(
    mode, tty, no_color, detail, monkeypatch, capsys
) -> None:
    """Replay highlights only the direct marker on a terminal that accepts color."""
    monkeypatch.setattr(sys.stderr, "isatty", lambda: tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    if no_color:
        monkeypatch.setenv("NO_COLOR", "")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    message = f"WARNING: Wayland ({mode}) {detail}"

    _repeat_startup_warnings([message])

    prefix = (
        "\x1b[31mWARNING:\x1b[0m" if mode == "wl-direct" and tty and not no_color else "WARNING:"
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == message.replace("WARNING:", prefix, 1) + "\n"


@pytest.mark.parametrize("warnings", [(), _WARNINGS])
def test_warning_replay_preserves_stderr(warnings, capsys) -> None:
    """Warnings retain their text and stream; an empty collection prints nothing."""
    _repeat_startup_warnings(warnings)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "".join(f"{warning}\n" for warning in warnings)


@pytest.fixture
def launch_with_warnings(mock_runtime):
    """Simulate the sandbox's immediate warnings and observer notifications."""

    def launch(*_args, hooks, **_kwargs) -> None:
        mock_runtime.container.return_value.state = "running"
        for warning in _WARNINGS:
            print(warning, file=sys.stderr)
            hooks.warning(warning)

    return launch


@pytest.mark.parametrize("mode", ["cli", "toad", "headless", "headless-follow"])
def test_launch_repeats_warnings_after_its_summary(
    mode: str, mock_runtime, launch_with_warnings
) -> None:
    """Every launch preserves both warning messages after its existing output tail."""
    module = {"cli": cli, "toad": toad}.get(mode, headless)
    container = mock_runtime.container.return_value
    container.state = None
    container.running = True
    container.stream_initial_logs.side_effect = lambda **_: print("initialization output") or True
    container.wait.side_effect = lambda: print("agent output") or 0
    output = StringIO()
    with (
        project_env(
            f"project:\n  id: {_PROJECT}\n  security_class: online\n",
            project_name=_PROJECT,
            with_config_file=True,
            clear_env=True,
        ),
        mock_git_config(),
        patch.object(module, "_run_container", side_effect=launch_with_warnings),
        patch.object(module, "_apply_shield_policy"),
        patch.object(toad, "assign_web_port", return_value=FAKE_PEER_PORT),
        redirect_stdout(output),
        redirect_stderr(output),
    ):
        if module is headless:
            headless.task_run_headless(
                headless.HeadlessRunRequest(_PROJECT, "test", follow=mode == "headless-follow")
            )
        else:
            task_id = task_new(_PROJECT)
            (cli.task_run_cli if module is cli else toad.task_run_toad)(_PROJECT, task_id)

    rendered = output.getvalue()
    assert all(rendered.count(warning) == 2 for warning in _WARNINGS)
    assert rendered.endswith("\n".join(_WARNINGS) + "\n")
    if module is not headless:
        assert rendered.index("initialization output") < rendered.rindex(_WARNINGS[0])
    elif mode == "headless-follow":
        assert rendered.index("agent output") < rendered.rindex(_WARNINGS[0])


@pytest.mark.parametrize("mode", ["cli", "toad", "run"])
def test_restart_repeats_warnings_after_its_footer(mode: str, launch_with_warnings) -> None:
    """Plain restarts repeat warnings without rechecking optional services."""
    output = StringIO()
    with (
        project_env(
            f"project:\n  id: {_PROJECT}\n",
            project_name=_PROJECT,
            with_config_file=True,
            clear_env=True,
        ),
        mock_git_config(),
        patch.object(restart, "_podman_start", side_effect=launch_with_warnings),
        patch.object(restart, "_apply_shield_policy"),
        patch.object(restart, "_warn_if_stale_image"),
        redirect_stdout(output),
        redirect_stderr(output),
    ):
        task_id = task_new(_PROJECT)
        meta, meta_path = load_task_meta(_PROJECT, task_id)
        restart._start_and_report_restart(
            load_project(_PROJECT), task_id, mode, "test-ctr", meta, meta_path
        )

    rendered = output.getvalue()
    assert all(rendered.count(warning) == 2 for warning in _WARNINGS)
    assert rendered.index("Restarted task") < rendered.rindex(_WARNINGS[0])
    assert rendered.endswith("\n".join(_WARNINGS) + "\n")


@pytest.mark.parametrize("follow", [False, True])
def test_followup_repeats_warnings_after_its_summary(
    follow: bool, mock_runtime, launch_with_warnings
) -> None:
    """Headless follow-ups retain the same warning tail as fresh headless launches."""
    output = StringIO()
    with (
        project_env(
            f"project:\n  id: {_PROJECT}\n  security_class: online\n",
            project_name=_PROJECT,
            with_config_file=True,
            clear_env=True,
        ),
        mock_git_config(),
        patch.object(headless, "_podman_start", side_effect=launch_with_warnings),
        patch.object(headless, "_apply_shield_policy"),
        patch.object(headless, "_refresh_before_resume"),
    ):
        task_id = headless.task_run_headless(
            headless.HeadlessRunRequest(_PROJECT, "first prompt", follow=False)
        )
        mock_runtime.container.return_value.state = "exited"
        with redirect_stdout(output), redirect_stderr(output):
            headless.task_followup_headless(_PROJECT, task_id, "next prompt", follow=follow)

    rendered = output.getvalue()
    assert all(rendered.count(warning) == 2 for warning in _WARNINGS)
    assert rendered.endswith("\n".join(_WARNINGS) + "\n")
