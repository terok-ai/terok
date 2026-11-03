# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for unattended (Level 1+2) features: terok task run and agent config."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest.mock
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from terok_sandbox import RunSpec

    from terok.lib.core.projects import ProjectConfig


from terok.lib.core.projects import load_project
from terok.lib.integrations.executor import agent_input_dir
from terok.lib.orchestration.task_runners import (
    HeadlessRunRequest,
    task_followup_headless,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)
from terok.lib.orchestration.tasks import task_new, task_rename
from tests.test_utils import assert_task_id, captured_runspec, mock_git_config, write_project
from tests.testfs import CONTAINER_TEROK_DIR, CONTAINER_VIBE_INSTRUCTIONS_PATH
from tests.testnet import FAKE_PEER_PORT


@dataclass
class TaskRunnerResult:
    """Captured result from a mocked headless task/follow-up invocation."""

    output: str
    run_mock: unittest.mock.Mock
    wait_mock: unittest.mock.Mock
    task_id: str | None = None

    @property
    def last_spec(self) -> RunSpec:
        """Return the ``RunSpec`` reconstructed from the most recent
        ``AgentRunner.launch_prepared(...)`` call captured by the mocked
        ``_agent_runner`` factory, rebuilt via
        `tests.test_utils.captured_runspec`."""
        return captured_runspec(self.run_mock)


def make_project_config(
    *,
    project_name: str,
    root: Path,
    tasks_root: Path,
    gate_path: Path,
    default_agent: str | None = None,
) -> ProjectConfig:
    """Build a ProjectConfig for wrapper/agent-config unit tests."""
    from terok.lib.core.projects import ProjectConfig

    return ProjectConfig(
        name=project_name,
        security_class="online",
        upstream_url=None,
        default_branch="main",
        root=root,
        tasks_root=tasks_root,
        gate_path=gate_path,
        staging_root=None,
        ssh_key_name=None,
        ssh_host_dir=None,
        default_agent=default_agent,
        human_name="Test User",
        human_email="test@example.com",
    )


def runner_env_vars(base: Path, config_file: Path) -> dict[str, str]:
    """Build TEROK_* env vars for task runner tests."""
    return {
        "TEROK_CONFIG_DIR": str(base / "config"),
        "TEROK_STATE_DIR": str(base / "state"),
        "TEROK_EXECUTOR_STATE_DIR": str(base / "agent"),
        "TEROK_CONFIG_FILE": str(config_file),
        "TEROK_SANDBOX_LIVE_DIR": str(base / "sandbox-live"),
        "TEROK_SANDBOX_STATE_DIR": str(base / "sandbox-state"),
    }


def task_paths(base: Path, project_name: str, task_id: str = "1") -> tuple[Path, Path]:
    """Return ``(agent_config_dir, meta_path)`` for a task.

    agent_config_dir lives under sandbox-live (container-writable),
    meta_path lives under state (terok-core metadata).
    """
    sandbox_live = base / "sandbox-live"
    state = base / "state"
    return (
        sandbox_live / "tasks" / project_name / task_id / "agent-config",
        state / "projects" / project_name / "tasks" / f"{task_id}_dossier.json",
    )


def write_runner_project(base: Path, project_name: str, extra_yml: str = "") -> Path:
    """Write a minimal project config and matching global config file for task runners."""
    config_base = base / "config"
    projects_root = config_base / "projects"
    envs_dir = base / "envs"
    projects_root.mkdir(parents=True, exist_ok=True)
    config_file = base / "config.yml"
    config_file.write_text(f"credentials:\n  dir: {envs_dir}\n", encoding="utf-8")
    write_project(
        projects_root,
        project_name,
        f"project:\n  id: {project_name}\n  security_class: online\n{extra_yml}",
    )
    return config_file


def read_task_meta(base: Path, project_name: str, task_id: str = "1") -> dict[str, object]:
    """Load merged task metadata for a task — wire-dossier + bookkeeping.

    Live tasks store the wire-dossier triple in JSON (the file shield
    consumers parse) and everything else in YAML; ``read_task_meta``
    composes them back into the orchestrator's logical dict shape.
    """
    from terok.lib.orchestration.tasks import read_task_meta

    meta_dir = task_paths(base, project_name, task_id)[1].parent
    return read_task_meta(meta_dir, task_id) or {}


def prepare_agent_config(
    project: ProjectConfig,
    task_id: str,
    *,
    instructions: str | None = None,
) -> Path:
    """Build an agent-config directory for the given task."""
    from terok_executor import AgentConfigSpec, prepare_agent_config_dir

    (project.tasks_root / task_id).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        return prepare_agent_config_dir(
            AgentConfigSpec(
                project.tasks_root,
                task_id,
                instructions=instructions,
                default_agent=project.default_agent,
                mounts_base=Path(td),
            )
        )


def run_headless_request(
    base: Path,
    config_file: Path,
    request: HeadlessRunRequest,
) -> TaskRunnerResult:
    """Run ``task_run_headless`` with the standard patched test harness.

    Relies on the ``mock_runtime`` autouse fixture to intercept container
    operations; container.wait() already returns 0 by default.
    """
    from terok.lib.core.runtime import resolve_runtime as get_runtime

    wait_mock = get_runtime().container.return_value.wait
    with unittest.mock.patch.dict(os.environ, runner_env_vars(base, config_file), clear=True):
        with (
            mock_git_config(),
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            buffer = StringIO()
            with redirect_stdout(buffer):
                task_id = task_run_headless(request)
    return TaskRunnerResult(
        task_id=task_id,
        output=buffer.getvalue(),
        run_mock=sandbox_factory,
        wait_mock=wait_mock,
    )


def run_followup_request(
    base: Path,
    project_name: str,
    task_id: str,
    prompt: str,
    *,
    container_state: list[str | None] | str | None,
    follow: bool = True,
) -> TaskRunnerResult:
    """Run ``task_followup_headless`` with the standard patched success harness."""
    from terok.lib.core.runtime import resolve_runtime as get_runtime

    runtime = get_runtime()
    container = runtime.container.return_value
    # ``start()`` is fire-and-forget now (raises on failure); default mock returns None.
    start_mock = container.start
    wait_mock = container.wait  # already returns 0 via fixture default

    state_patch: contextlib.AbstractContextManager
    if isinstance(container_state, list):
        state_patch = unittest.mock.patch.object(
            type(container),
            "state",
            new_callable=unittest.mock.PropertyMock,
            side_effect=iter(container_state).__next__,
            create=True,
        )
    else:
        container.state = container_state
        state_patch = contextlib.nullcontext()

    with (
        state_patch,
        unittest.mock.patch.dict(
            os.environ, runner_env_vars(base, base / "config.yml"), clear=True
        ),
        mock_git_config(),
    ):
        buffer = StringIO()
        with redirect_stdout(buffer):
            task_followup_headless(project_name, task_id, prompt, follow=follow)
    return TaskRunnerResult(output=buffer.getvalue(), run_mock=start_mock, wait_mock=wait_mock)


def _spec_volumes(result: TaskRunnerResult) -> tuple:
    """Extract VolumeSpec tuple from the RunSpec captured by the sandbox mock."""
    return result.last_spec.volumes


def _spec_command(result: TaskRunnerResult) -> tuple[str, ...]:
    """Extract command from the RunSpec captured by the sandbox mock."""
    return result.last_spec.command


def _spec_container_name(result: TaskRunnerResult) -> str:
    """Extract container_name from the RunSpec captured by the sandbox mock."""
    return result.last_spec.container_name


class TestAgentConfigProject:
    """Tests for agent config parsing in projects.py."""

    def test_agent_config_empty_when_absent(self) -> None:
        """Project has agent_config={} when not configured."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            projects_root = config_root / "projects"
            write_project(projects_root, "proj_noagent", "project:\n  id: proj_noagent\n")

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_root), "TEROK_STATE_DIR": str(base / "s")},
            ):
                with mock_git_config():
                    p = load_project("proj_noagent")
                assert p.agent_config == {}

    def test_agent_config_parsed_as_dict(self) -> None:
        """Project parses agent: section as a dict."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_root = base / "config"
            projects_root = config_root / "projects"

            write_project(
                projects_root,
                "proj_agent",
                ("project:\n  id: proj_agent\nagent:\n  model: opus\n  timeout: 900\n"),
            )

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_root), "TEROK_STATE_DIR": str(base / "s")},
            ):
                with mock_git_config():
                    p = load_project("proj_agent")
                assert p.agent_config["model"] == "opus"
                assert p.agent_config["timeout"] == 900


class TestTaskInstructionMounts:
    """All launch modes delegate instruction mounts to the executor."""

    @pytest.mark.parametrize("mode", ["cli", "headless", "toad"])
    @pytest.mark.parametrize("isolation", ["shared", "sealed"])
    def test_vibe_receives_task_instructions(
        self, tmp_path: Path, mock_runtime, mode: str, isolation: str
    ) -> None:
        """Vibe gets a private system-context file even when not the default agent."""
        project_name = "proj_instruction_mount"
        instructions = "Follow the task-specific environment instructions."
        config_file = write_runner_project(
            tmp_path,
            project_name,
            f"  isolation: {isolation}\nagent:\n  instructions: {instructions}\n",
        )
        mock_runtime.container.return_value.state = None
        mock_runtime.container.return_value.running = True
        with (
            unittest.mock.patch.dict(
                os.environ, runner_env_vars(tmp_path, config_file), clear=True
            ),
            mock_git_config(),
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as runner,
            unittest.mock.patch(
                "terok.lib.orchestration.task_runners.toad.assign_web_port",
                return_value=FAKE_PEER_PORT,
            ),
            unittest.mock.patch("terok.lib.orchestration.task_runners.cli._assert_running"),
        ):
            if mode == "headless":
                task_id = task_run_headless(HeadlessRunRequest(project_name, "Test instructions"))
            else:
                task_id = task_new(project_name)
                launch = task_run_cli if mode == "cli" else task_run_toad
                launch(project_name, task_id)

            config_dir = load_project(project_name).tasks_root / task_id / "agent-config"
            spec = captured_runspec(runner)
            assert spec.sealed is (isolation == "sealed")
            volumes = spec.volumes
            config_mounts = [v for v in volumes if v.container_path == str(CONTAINER_TEROK_DIR)]
            assert len(config_mounts) == 1
            assert config_mounts[0].host_path == config_dir
            vibe_mounts = [
                v for v in volumes if v.container_path == str(CONTAINER_VIBE_INSTRUCTIONS_PATH)
            ]
            assert len(vibe_mounts) == 1
            mount = vibe_mounts[0]
            assert mount.host_path == agent_input_dir(config_dir.parent) / "instructions.md"
            assert mount.host_path.read_text() == instructions
            assert mount.read_only
            assert mount.sharing == "private"


class TestTaskRunHeadless:
    """Tests for task_run_headless."""

    def test_headless_creates_task_and_writes_prompt(self) -> None:
        """task_run_headless creates a task with prompt.txt in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_hl"),
                HeadlessRunRequest("proj_hl", "Fix the auth bug"),
            )

            assert_task_id(result.task_id)
            agent_config_dir, meta_path = task_paths(base, "proj_hl", result.task_id)
            prompt_file = agent_input_dir(agent_config_dir.parent) / "prompt.txt"
            assert prompt_file.is_file()
            assert prompt_file.read_text() == "Fix the auth bug"

    def test_headless_mounts_agent_config_dir(self) -> None:
        """task_run_headless mounts agent-config dir to /home/dev/.terok."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_mount"),
                HeadlessRunRequest("proj_mount", "test prompt"),
            )
            assert any(
                v.container_path == "/home/dev/.terok" and v.sharing == "private"
                for v in result.last_spec.volumes
            )

    def test_headless_generates_agent_wrapper(self) -> None:
        """task_run_headless generates terok-executor.sh in agent-config dir."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_wrap"),
                HeadlessRunRequest("proj_wrap", "test"),
            )

            wrapper = (
                agent_input_dir(task_paths(base, "proj_wrap", result.task_id)[0].parent)
                / "terok-executor.sh"
            )
            assert wrapper.is_file()
            content = wrapper.read_text()
            assert "claude()" in content
            assert "--dangerously-skip-permissions" not in content

    def test_headless_writes_session_hook_settings(self) -> None:
        """task_run_headless writes shared Claude settings with SessionStart hook."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_headless_request(
                base,
                write_runner_project(base, "proj_hook"),
                HeadlessRunRequest("proj_hook", "test"),
            )

            settings = base / "sandbox-live" / "mounts" / "_claude-config" / "settings.json"
            assert settings.is_file()
            assert "SessionStart" in json.loads(settings.read_text())["hooks"]

    def test_headless_cli_model_in_command(self) -> None:
        """CLI model appears in headless bash command, not in wrapper."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_flags"),
                HeadlessRunRequest("proj_flags", "test", model="opus"),
            )

            bash_cmd = result.last_spec.command[-1]
            assert "--model opus" in bash_cmd
            assert "--terok-timeout" in bash_cmd

            wrapper = (
                agent_input_dir(task_paths(base, "proj_flags", result.task_id)[0].parent)
                / "terok-executor.sh"
            )
            content = wrapper.read_text()
            assert "--model" not in content
            assert "--terok-timeout" in content

    def test_headless_container_name_uses_run_prefix(self) -> None:
        """task_run_headless names the container <project>-run-<task_id>."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_name"),
                HeadlessRunRequest("proj_name", "test"),
            )
            assert_task_id(result.task_id)
            assert result.last_spec.container_name == f"proj_name-run-{result.task_id}"

    def test_headless_metadata_updated(self) -> None:
        """task_run_headless sets mode=run and updates status on completion."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_meta"),
                HeadlessRunRequest("proj_meta", "test"),
            )

            meta = read_task_meta(base, "proj_meta", result.task_id)
            assert meta["mode"] == "run"
            assert meta["exit_code"] == 0

    def test_headless_preserves_name_changed_during_launch(self, tmp_path: Path) -> None:
        """Persisting launch state must not undo a rename made during container startup."""
        project_name = "proj_headless_rename"

        def rename_during_launch(**kwargs) -> None:
            """Rename after the runner has read the original metadata."""
            task_rename(project_name, kwargs["task_id"], "edited-name")

        with unittest.mock.patch(
            "terok.lib.orchestration.task_runners.headless._run_container",
            side_effect=rename_during_launch,
        ):
            result = run_headless_request(
                tmp_path,
                write_runner_project(tmp_path, project_name),
                HeadlessRunRequest(project_name, "test", name="original-name", follow=False),
            )

        meta = read_task_meta(tmp_path, project_name, result.task_id)
        assert meta["name"] == "edited-name"
        assert meta["mode"] == "run"
        assert meta["ready_at"]
        assert meta["agent"] == "claude"

    def test_headless_debug_flows_to_sidecar_and_meta(self) -> None:
        """``--debug`` reaches ``launch_prepared`` and is persisted in task meta."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_dbg"),
                HeadlessRunRequest("proj_dbg", "test", debug=True),
            )

            kwargs = result.run_mock.return_value.launch_prepared.call_args.kwargs
            assert kwargs["allow_debugger"] is True
            assert read_task_meta(base, "proj_dbg", result.task_id)["debug"] is True

    def test_headless_no_follow_mode(self) -> None:
        """task_run_headless with follow=False prints detach info."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_nf"),
                HeadlessRunRequest("proj_nf", "test", follow=False),
            )

            result.wait_mock.assert_not_called()
            assert "detached" in result.output.lower()
            assert_task_id(result.task_id)
            assert f"proj_nf-run-{result.task_id}" in result.output

    def test_headless_provider_override_sets_terok_provider_env(self) -> None:
        """``--provider`` flows into the container as ``TEROK_PROVIDER``."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_prov"),
                HeadlessRunRequest("proj_prov", "test", provider="openrouter"),
            )
            assert result.last_spec.env["TEROK_PROVIDER"] == "openrouter"

    def test_headless_default_provider_sets_terok_provider_env(self) -> None:
        """A project ``default_provider`` reaches the container without a flag."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_dp", "default_provider: openrouter\n"),
                HeadlessRunRequest("proj_dp", "test"),
            )
            assert result.last_spec.env["TEROK_PROVIDER"] == "openrouter"

    def test_headless_no_provider_omits_terok_provider_env(self) -> None:
        """No provider selected → ``TEROK_PROVIDER`` is absent from the env."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_np"),
                HeadlessRunRequest("proj_np", "test"),
            )
            assert "TEROK_PROVIDER" not in result.last_spec.env

    def test_headless_uses_claude_function_in_command(self) -> None:
        """task_run_headless uses claude wrapper via --terok-timeout."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            result = run_headless_request(
                base,
                write_runner_project(base, "proj_cmd"),
                HeadlessRunRequest("proj_cmd", "test"),
            )
            bash_cmd = result.last_spec.command[-1]
            assert "init-ssh-and-repo.sh" in bash_cmd
            assert "start-claude.sh" not in bash_cmd
            assert "--terok-timeout" in bash_cmd
            assert "--output-format stream-json" in bash_cmd
            assert "-p" in bash_cmd
            assert "--dangerously-skip-permissions" not in bash_cmd
            assert '--add-dir "/"' not in bash_cmd
            assert "GIT_AUTHOR_NAME=Claude" not in bash_cmd

    def test_headless_config_file_overrides_agent_settings(self) -> None:
        """task_run_headless applies overrides from the --config YAML file."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            agent_config = base / "my-agent-config.yml"
            agent_config.write_text("model: opus\n", encoding="utf-8")

            result = run_headless_request(
                base,
                write_runner_project(base, "proj_cfgfile"),
                HeadlessRunRequest("proj_cfgfile", "test", config_path=str(agent_config)),
            )

            bash_cmd = result.last_spec.command[-1]
            assert "--model opus" in bash_cmd


class TestTaskFollowupHeadless:
    """Tests for task_followup_headless."""

    def _create_completed_task(self, base: Path, project_name: str) -> str:
        """Create a task via task_run_headless and return the task_id."""
        result = run_headless_request(
            base,
            write_runner_project(base, project_name),
            HeadlessRunRequest(project_name, "initial prompt"),
        )
        assert result.task_id is not None
        return result.task_id

    def test_followup_writes_new_prompt(self) -> None:
        """Follow-up replaces prompt.txt and archives old prompt to history."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            task_id = self._create_completed_task(base, "proj_fu")

            run_followup_request(
                base,
                "proj_fu",
                task_id,
                "fix the remaining tests",
                container_state=["exited", "running"],
            )

            agent_cfg, meta_path = task_paths(base, "proj_fu", task_id)
            assert (
                agent_input_dir(agent_cfg.parent) / "prompt.txt"
            ).read_text() == "fix the remaining tests"
            history = (agent_cfg.parent / "prompt-history.txt").read_text()
            assert "initial prompt" in history
            assert "---" in history

    def test_followup_uses_container_start(self, mock_runtime) -> None:
        """Follow-up starts the task's container via the runtime."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_start")
            result = run_followup_request(
                base, "proj_start", task_id, "continue", container_state=["exited", "running"]
            )
            result.run_mock.assert_called_once()
            mock_runtime.container.assert_any_call(f"proj_start-run-{task_id}")

    def test_followup_refreshes_the_bundle_and_sidecar_before_starting(self, mock_runtime) -> None:
        """The follow-up start applies today's shield tiers and clearance mode, as a restart does.

        ``podman start`` replays the creation-time bundle and sidecar: an
        override shortened or removed since, or a project switched to
        ``services.clearance: off``, would otherwise be replayed.  The refresh
        runs before the prompt is handed over, so a refusal leaves the task
        as it was.
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_refresh")
            order: list[str] = []
            container = mock_runtime.container.return_value
            container.start.side_effect = lambda: order.append("start")
            restart_mod = "terok.lib.orchestration.task_runners.restart"
            with (
                unittest.mock.patch(
                    f"{restart_mod}._validate_shield_bundle_version",
                    side_effect=lambda _c: order.append("version"),
                ) as gate,
                unittest.mock.patch(
                    f"{restart_mod}._refresh_shield_tiers",
                    side_effect=lambda *_a: order.append("tiers"),
                ) as tiers,
                unittest.mock.patch(
                    f"{restart_mod}._refresh_clearance_mode",
                    side_effect=lambda *_a: order.append("clearance"),
                ) as clearance,
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.headless._inject_followup_prompt",
                    side_effect=lambda **_kw: order.append("prompt"),
                ),
            ):
                run_followup_request(
                    base,
                    "proj_refresh",
                    task_id,
                    "continue",
                    container_state=["exited", "running"],
                    follow=False,
                )

            cname = f"proj_refresh-run-{task_id}"
            gate.assert_called_once_with(cname)
            project, tier_cname, task_dir = tiers.call_args.args
            assert tier_cname == cname
            assert task_dir == project.tasks_root / task_id
            assert clearance.call_args.args[1] == cname
            assert order == ["version", "tiers", "clearance", "prompt", "start"]

    def test_followup_refused_by_the_bundle_gate_leaves_the_prompt_alone(
        self, mock_runtime
    ) -> None:
        """A stale bundle fails the follow-up before anything is written or started."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_stale")
            with (
                unittest.mock.patch(
                    "terok.lib.orchestration.task_runners.restart._validate_shield_bundle_version",
                    side_effect=SystemExit("carries shield bundle v1"),
                ),
                pytest.raises(SystemExit, match="shield bundle"),
            ):
                run_followup_request(
                    base, "proj_stale", task_id, "continue", container_state="exited"
                )

            agent_cfg, _meta_path = task_paths(base, "proj_stale", task_id)
            assert (
                agent_input_dir(agent_cfg.parent) / "prompt.txt"
            ).read_text() == "initial prompt"
            mock_runtime.container.return_value.start.assert_not_called()

    def test_followup_rejects_non_run_mode(self) -> None:
        """Follow-up rejects tasks that aren't headless (mode != 'run')."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            config_file = write_runner_project(base, "proj_mode")

            from terok.lib.orchestration.tasks import task_new, write_task_meta

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, config_file), clear=True
            ):
                with mock_git_config():
                    task_id = task_new("proj_mode")
                    _agent_cfg, dossier_handle = task_paths(base, "proj_mode", task_id)
                    meta = read_task_meta(base, "proj_mode", task_id)
                    meta["mode"] = "cli"
                    write_task_meta(dossier_handle, meta)

                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_mode", task_id, "test")
                    assert "not a headless task" in str(ctx.value)

    def test_followup_rejects_running_task(self, mock_runtime) -> None:
        """Follow-up rejects tasks that are still running."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            config_file = write_runner_project(base, "proj_run")

            from terok.lib.orchestration.tasks import task_new, write_task_meta

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, config_file), clear=True
            ):
                with mock_git_config():
                    task_id = task_new("proj_run")
                    _agent_cfg, dossier_handle = task_paths(base, "proj_run", task_id)
                    meta = read_task_meta(base, "proj_run", task_id)
                    meta["mode"] = "run"
                    write_task_meta(dossier_handle, meta)

                    mock_runtime.container.return_value.state = "running"
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_run", task_id, "test")
                    assert "still running" in str(ctx.value)

    def test_followup_rejects_running_container(self, mock_runtime) -> None:
        """Follow-up rejects when container is still running (stale metadata)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_crun")
            mock_runtime.container.return_value.state = "running"

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with mock_git_config():
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_crun", task_id, "test")
                    assert "still running" in str(ctx.value)

    def test_followup_updates_metadata(self) -> None:
        """Follow-up updates task status to running then completed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            task_id = self._create_completed_task(base, "proj_meta2")

            run_followup_request(
                base, "proj_meta2", task_id, "continue", container_state=["exited", "running"]
            )

            meta = read_task_meta(base, "proj_meta2", task_id)
            assert meta["exit_code"] == 0

    def test_followup_preserves_name_changed_during_restart(
        self, tmp_path: Path, mock_runtime
    ) -> None:
        """Clearing the old exit code must not undo a rename made during restart."""
        project_name = "proj_followup_rename"
        task_id = self._create_completed_task(tmp_path, project_name)
        mock_runtime.container.return_value.start.side_effect = lambda: task_rename(
            project_name, task_id, "edited-name"
        )

        run_followup_request(
            tmp_path,
            project_name,
            task_id,
            "continue",
            container_state=["exited", "running"],
            follow=False,
        )

        meta = read_task_meta(tmp_path, project_name, task_id)
        assert meta["name"] == "edited-name"
        assert meta["mode"] == "run"
        assert meta["exit_code"] is None

    def test_followup_no_follow_mode(self, mock_runtime) -> None:
        """Follow-up with follow=False prints detached info and skips wait."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            task_id = self._create_completed_task(base, "proj_meta2")
            # Clear wait-call history from the setup phase so the assertion
            # below only sees calls made by the follow-up itself.
            mock_runtime.container.return_value.wait.reset_mock()

            result = run_followup_request(
                base,
                "proj_meta2",
                task_id,
                "continue",
                container_state=["exited", "running"],
                follow=False,
            )

            result.wait_mock.assert_not_called()
            assert "detached" in result.output.lower()

            meta = read_task_meta(base, "proj_meta2", task_id)
            assert meta["exit_code"] is None

    def test_followup_container_not_found(self, mock_runtime) -> None:
        """Follow-up raises SystemExit with 'not found' when container has been removed."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_notfound")
            mock_runtime.container.return_value.state = None

            with unittest.mock.patch.dict(
                os.environ, runner_env_vars(base, base / "config.yml"), clear=True
            ):
                with mock_git_config():
                    with pytest.raises(SystemExit) as ctx:
                        task_followup_headless("proj_notfound", task_id, "test")
                    assert "not found" in str(ctx.value)

    def test_followup_start_fails(self, mock_runtime) -> None:
        """Follow-up raises SystemExit when container remains exited after start."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            task_id = self._create_completed_task(base, "proj_startfail")
            container_mock = mock_runtime.container.return_value

            with (
                unittest.mock.patch.object(
                    type(container_mock),
                    "state",
                    new_callable=unittest.mock.PropertyMock,
                    side_effect=iter(["exited", "exited"]).__next__,
                    create=True,
                ),
                unittest.mock.patch.dict(
                    os.environ, runner_env_vars(base, base / "config.yml"), clear=True
                ),
                mock_git_config(),
            ):
                with pytest.raises(SystemExit) as ctx:
                    task_followup_headless("proj_startfail", task_id, "test")
                assert "failed to start" in str(ctx.value)

    def test_sealed_followup_updates_live_readonly_input(self, mock_runtime) -> None:
        """Sealed follow-ups replace the live read-only prompt and archive host-only history."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)

            config_file = write_runner_project(
                base, "proj_sealed", extra_yml="  isolation: sealed\n"
            )

            # Create a completed headless task
            result = run_headless_request(
                base,
                config_file,
                HeadlessRunRequest("proj_sealed", "initial prompt"),
            )
            task_id = result.task_id
            assert task_id is not None

            container_mock = mock_runtime.container.return_value

            with (
                unittest.mock.patch.object(
                    type(container_mock),
                    "state",
                    new_callable=unittest.mock.PropertyMock,
                    side_effect=iter(["exited", "running"]).__next__,
                    create=True,
                ),
                unittest.mock.patch.dict(
                    os.environ, runner_env_vars(base, config_file), clear=True
                ),
                mock_git_config(),
            ):
                buffer = StringIO()
                with redirect_stdout(buffer):
                    task_followup_headless("proj_sealed", task_id, "sealed follow-up")

                    agent_cfg, _ = task_paths(base, "proj_sealed", task_id)
                    assert (
                        agent_input_dir(agent_cfg.parent) / "prompt.txt"
                    ).read_text() == "sealed follow-up"
                    assert "initial prompt" in (agent_cfg.parent / "prompt-history.txt").read_text()
