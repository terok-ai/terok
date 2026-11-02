# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task-controlled files cannot become host commands, prompt history or writes."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from terok_util import UnsafePathError

from terok.lib.core.work_status import (
    PendingPhase,
    WorkStatus,
    read_pending_phase,
    read_work_status,
    write_pending_phase,
    write_work_status,
)
from terok.lib.integrations.executor import agent_input_dir
from terok.lib.orchestration.environment import _seed_workspace_cache
from terok.lib.orchestration.task_runners.headless import _inject_followup_prompt
from terok.lib.util.host_cmd import WORKSPACE_SEED_PENDING


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "oversize", "invalid-date"])
def test_bad_status_is_only_bad_telemetry(tmp_path, kind):
    """Unsafe files and malformed implicit YAML dates do not break task discovery."""
    directory = tmp_path / "agent-config"
    directory.mkdir()
    target = directory / "work-status.yml"
    victim = tmp_path / "victim"
    victim.write_text("status: coding")
    match kind:
        case "symlink":
            target.symlink_to(victim)
        case "hardlink":
            os.link(victim, target)
        case "fifo":
            os.mkfifo(target)
        case "oversize":
            target.write_text("a" * (64 * 1024 + 1))
        case "invalid-date":
            target.write_text("message: 2026-99-99")
    assert read_work_status(directory) == WorkStatus()
    assert victim.read_text() == "status: coding"


def test_status_writer_refuses_links(tmp_path):
    """Writing or clearing telemetry cannot follow a linked leaf or directory."""
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    directory = tmp_path / "agent-config"
    directory.mkdir()
    (directory / "work-status.yml").symlink_to(victim)
    for status in ("testing", None):
        with pytest.raises(UnsafePathError):
            write_work_status(directory, status)
    assert victim.read_text() == "unchanged"
    link = tmp_path / "linked-config"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        write_work_status(link, "coding")


def test_task_cannot_forge_pending_host_commands(tmp_path):
    """Only the host-only control directory feeds deferred transitions."""
    directory = tmp_path / "agent-config"
    directory.mkdir()
    (directory / "pending-phase.yml").write_text("phase: testing\nprompt: forged")
    assert read_pending_phase(directory) is None
    write_pending_phase(directory, "reviewing", "host instruction")
    assert read_pending_phase(directory) == PendingPhase("reviewing", "host instruction")
    assert (tmp_path / "host-control" / "pending-phase.yml").is_file()


def test_headless_history_ignores_task_authored_prompt_and_history(tmp_path):
    """A follow-up archives only the immutable mount's host-owned prior prompt."""
    directory = tmp_path / "agent-config"
    directory.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    (directory / "prompt.txt").write_text("forged instruction")
    (directory / "prompt-history.txt").symlink_to(victim)
    inputs = agent_input_dir(tmp_path)
    inputs.mkdir()
    (inputs / "prompt.txt").write_text("host instruction")
    _inject_followup_prompt(task_dir=tmp_path, prompt="next instruction")
    assert (inputs / "prompt.txt").read_text() == "next instruction"
    assert (tmp_path / "prompt-history.txt").read_text() == "host instruction\n\n---\n\n"
    assert victim.read_text() == "unchanged"


def test_workspace_marker_cannot_authorize_host_cache_seeding(tmp_path):
    """Only a host-created one-shot lifecycle grant permits the optimization."""
    workspace = tmp_path / "workspace-dangerous"
    workspace.mkdir()
    (workspace / ".new-task-marker").touch()
    with (
        patch("terok.lib.integrations.executor.seed_workspace_from_clone_cache") as seed,
        patch("terok.lib.orchestration.environment.make_sandbox_config"),
    ):
        _seed_workspace_cache(workspace, "project", None)
        seed.assert_not_called()
        (tmp_path / WORKSPACE_SEED_PENDING).touch()
        _seed_workspace_cache(workspace, "project", None)
        seed.assert_called_once()
        _seed_workspace_cache(workspace, "project", None)
        seed.assert_called_once()


@pytest.mark.parametrize("overflow", ["prompt", "history"])
def test_headless_limits_fail_before_either_file_changes(tmp_path, monkeypatch, overflow):
    """Oversized follow-ups cannot strand an unreadable current prompt or history."""
    from terok.lib.orchestration.task_runners import headless

    inputs = agent_input_dir(tmp_path)
    inputs.mkdir()
    current = inputs / "prompt.txt"
    current.write_text("host prompt")
    history = tmp_path / "prompt-history.txt"
    history.write_text("previous")
    limit = "_MAX_PROMPT_BYTES" if overflow == "prompt" else "_MAX_HISTORY_BYTES"
    monkeypatch.setattr(headless, limit, 2 if overflow == "prompt" else 10)
    with pytest.raises(ValueError, match="exceeds"):
        _inject_followup_prompt(task_dir=tmp_path, prompt="next prompt")
    assert current.read_text() == "host prompt"
    assert history.read_text() == "previous"


@pytest.mark.parametrize("link", ["workspace", "marker"])
def test_task_allocation_refuses_leftover_workspace_links(tmp_path, link):
    """Even a reused orphan directory cannot redirect initialization writes or chmod."""
    from types import SimpleNamespace

    from terok.lib.orchestration.tasks import lifecycle

    project = SimpleNamespace(name="project", tasks_root=tmp_path / "tasks")
    task = project.tasks_root / "collision"
    task.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    secret = victim / "private"
    secret.write_text("unchanged")
    workspace = task / "workspace-dangerous"
    if link == "workspace":
        workspace.symlink_to(victim, target_is_directory=True)
    else:
        workspace.mkdir()
        (workspace / ".new-task-marker").symlink_to(secret)
    with (
        patch.object(lifecycle, "tasks_meta_dir", return_value=tmp_path / "meta"),
        patch.object(lifecycle, "_generate_unique_id", return_value="collision"),
        pytest.raises(UnsafePathError),
    ):
        lifecycle._task_new(project, name="test-task")
    assert secret.read_text() == "unchanged"
    assert victim.stat().st_mode & 0o777 == 0o755
