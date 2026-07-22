# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for higher-level CLI workflow shortcuts."""

from __future__ import annotations

import unittest.mock
from collections.abc import Callable

import pytest

from tests.testfs import FAKE_GATE_DIR


@pytest.fixture(autouse=True)
def _bypass_setup_verdict_gate():
    """Skip the stamp-based gate — covered separately in ``test_cli_task_verdict_gate.py``.

    Workflow tests assert the command-dispatch shape downstream of the
    gate; they run in a stamp-free tmp env where the real gate would
    always raise exit 3 before dispatch ever happens.
    """
    with (
        unittest.mock.patch("terok.cli.commands.task._setup_verdict_or_exit"),
        unittest.mock.patch("terok.cli.commands.task.require_project_exists"),
        unittest.mock.patch("terok.cli.commands.setup.require_project_exists"),
    ):
        yield


def _patch_init_steps[T](func: Callable[..., T]) -> Callable[..., T]:
    """Apply project-init step mocks to a test method.

    Iterative-wrap order: the first ``patch(...)`` call becomes the
    innermost decorator and passes the first mock arg.  Test signature
    therefore reads: ``(self, mock_summarize, mock_gen, mock_build,
    mock_get_project)``.
    """
    func = unittest.mock.patch("terok.cli.commands.setup.summarize_ssh_init")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.generate_dockerfiles")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.build_images")(func)
    func = unittest.mock.patch("terok.cli.commands.setup.get_project")(func)
    return func


def _make_init_project_mock(
    *, gate_enabled: bool = True, sync_result: dict | None = None
) -> unittest.mock.Mock:
    """Build a Project mock that the new ``cmd_project_init`` exercises."""
    project = unittest.mock.Mock()
    project.config.gate_enabled = gate_enabled
    project.provision_ssh_key.return_value = _FAKE_SSH_INIT_RESULT
    project.gate.sync.return_value = sync_result or _quiet_sync_result()
    return project


def _quiet_sync_result() -> dict:
    """A successful GateSyncResult that applied nothing and pends nothing."""
    return {
        "success": True,
        "path": str(FAKE_GATE_DIR),
        "upstream_url": None,
        "created": False,
        "migrated": False,
        "errors": [],
        "notes": [],
        "applied": [],
        "pending": [],
        "gate_only_branches": [],
        "cache_refreshed": False,
        "cache_error": None,
    }


def run_main(argv: list[str]) -> None:
    """Run the CLI entrypoint with a patched ``sys.argv``."""
    from terok.cli.main import main

    with unittest.mock.patch("sys.argv", argv):
        main()


_FAKE_SSH_INIT_RESULT = {
    "key_id": 42,
    "key_type": "ed25519",
    "fingerprint": "fp",
    "comment": "c",
    "public_line": "ssh-ed25519 AAAA c",
}


class TestProjectInit:
    """Tests for the project-init convenience command."""

    @_patch_init_steps
    def test_cmd_project_init_calls_four_steps(
        self,
        mock_summarize,
        mock_gen,
        mock_build,
        mock_get_project,
    ) -> None:
        project = _make_init_project_mock()
        mock_get_project.return_value = project

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("myproj")

        project.provision_ssh_key.assert_called_once_with()
        mock_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)
        project.pause_for_ssh_key_registration_if_needed.assert_called_once_with()
        mock_gen.assert_called_once_with("myproj")
        mock_build.assert_called_once_with("myproj")
        project.gate.sync.assert_called_once()

    @_patch_init_steps
    def test_cmd_project_init_calls_in_order(
        self,
        mock_summarize,
        mock_gen,
        mock_build,
        mock_get_project,
    ) -> None:
        project = _make_init_project_mock()
        mock_get_project.return_value = project
        call_order: list[str] = []
        project.provision_ssh_key.side_effect = lambda *a, **kw: (
            call_order.append("ssh"),
            _FAKE_SSH_INIT_RESULT,
        )[-1]
        mock_summarize.side_effect = lambda *a, **kw: call_order.append("summarize")
        project.pause_for_ssh_key_registration_if_needed.side_effect = lambda *a, **kw: (
            call_order.append("pause")
        )
        mock_gen.side_effect = lambda *a, **kw: call_order.append("generate")
        mock_build.side_effect = lambda *a, **kw: call_order.append("build")
        project.gate.sync.side_effect = lambda **kw: (
            call_order.append("gate"),
            _quiet_sync_result(),
        )[-1]

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("proj1")

        assert call_order == ["ssh", "summarize", "pause", "generate", "build", "gate"]

    @_patch_init_steps
    def test_cmd_project_init_gate_failure_raises(
        self,
        mock_summarize,
        mock_gen,
        mock_build,
        mock_get_project,
    ) -> None:
        project = _make_init_project_mock(
            sync_result={"success": False, "errors": ["no upstream_url"]}
        )
        mock_get_project.return_value = project

        from terok.cli.commands.setup import cmd_project_init

        with pytest.raises(SystemExit, match="Gate sync failed") as exc_info:
            cmd_project_init("badproj")
        # The hint pointing at gate.enabled: false is what makes the
        # failure actionable for users in Andreas' situation (#790).
        assert "gate.enabled: false" in str(exc_info.value)

    @_patch_init_steps
    def test_cmd_project_init_skips_gate_sync_when_disabled(
        self,
        mock_summarize,
        mock_gen,
        mock_build,
        mock_get_project,
    ) -> None:
        """``gate.enabled: false`` short-circuits after build — no sync attempted."""
        project = _make_init_project_mock(gate_enabled=False)
        mock_get_project.return_value = project

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("noproj")

        mock_build.assert_called_once_with("noproj")
        project.gate.sync.assert_not_called()


class TestCliSshInit:
    """Tests for the ``project ssh-init`` CLI command."""

    @unittest.mock.patch("terok.cli.commands.project.summarize_ssh_init")
    @unittest.mock.patch("terok.cli.commands.project.get_project")
    def test_ssh_init_delegates_to_project(self, mock_get_project, mock_summarize) -> None:
        """dispatch → get_project(id).provision_ssh_key(**defaults) → summarize(result)."""
        import argparse

        project = unittest.mock.Mock()
        project.provision_ssh_key.return_value = _FAKE_SSH_INIT_RESULT
        mock_get_project.return_value = project

        from terok.cli.commands.project import dispatch

        args = argparse.Namespace(
            cmd="project",
            project_cmd="ssh-init",
            project_name="proj",
            key_type="ed25519",
            comment=None,
            force=False,
        )
        assert dispatch(args) is True
        mock_get_project.assert_called_once_with("proj")
        project.provision_ssh_key.assert_called_once_with(
            key_type="ed25519", comment=None, force=False
        )
        mock_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)


class TestSshPause:
    """Tests for the SSH key registration pause helper."""

    @unittest.mock.patch("builtins.input", return_value="")
    def test_pauses_for_ssh_upstream(self, mock_input) -> None:
        from terok.lib.domain.project import Project

        for upstream in ("git@github.com:org/repo.git", "ssh://github.com/org/repo.git"):
            mock_input.reset_mock()
            config = unittest.mock.Mock(upstream_url=upstream)
            Project(config).pause_for_ssh_key_registration_if_needed()
            mock_input.assert_called_once_with("Press Enter once the key is registered... ")

    @unittest.mock.patch("builtins.input", return_value="")
    def test_no_pause_for_https_upstream(self, mock_input) -> None:
        from terok.lib.domain.project import Project

        config = unittest.mock.Mock(upstream_url="https://github.com/org/repo.git")
        Project(config).pause_for_ssh_key_registration_if_needed()
        mock_input.assert_not_called()

    @_patch_init_steps
    def test_project_init_continues_after_pause(
        self,
        mock_summarize,
        mock_gen,
        mock_build,
        mock_get_project,
    ) -> None:
        project = _make_init_project_mock()
        mock_get_project.return_value = project

        from terok.cli.commands.setup import cmd_project_init

        cmd_project_init("sshproj")

        project.provision_ssh_key.assert_called_once_with()
        mock_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)
        project.pause_for_ssh_key_registration_if_needed.assert_called_once_with()
        mock_gen.assert_called_once_with("sshproj")
        mock_build.assert_called_once_with("sshproj")
        project.gate.sync.assert_called_once()


class TestTaskRunInteractive:
    """``task run --mode cli|toad`` creates a new task and invokes the runner."""

    @pytest.mark.parametrize(
        ("argv", "task_id", "runner_path", "expected_call"),
        [
            (
                ["terok", "task", "run", "proj1"],
                "42",
                "terok.cli.commands.task.task_run_cli",
                ("proj1", "42", {"unrestricted": None, "debug": False}),
            ),
            (
                ["terok", "task", "run", "proj1", "--mode", "toad"],
                "10",
                "terok.cli.commands.task.task_run_toad",
                ("proj1", "10", {"unrestricted": None, "debug": False}),
            ),
            (
                ["terok", "task", "run", "proj1", "--debug"],
                "42",
                "terok.cli.commands.task.task_run_cli",
                ("proj1", "42", {"unrestricted": None, "debug": True}),
            ),
        ],
        ids=["default-cli-mode", "toad-mode", "cli-debug-mode"],
    )
    def test_task_run_interactive_dispatch(
        self,
        argv: list[str],
        task_id: str,
        runner_path: str,
        expected_call: tuple[str, str, dict[str, object]],
    ) -> None:
        # --no-attach keeps the CLI test deterministic regardless of TTY
        # state in the pytest harness.
        argv = [*argv, "--no-attach"]
        with (
            unittest.mock.patch("terok.cli.commands.task.project_image_exists", return_value=True),
            unittest.mock.patch(
                "terok.cli.commands.task.task_new", return_value=task_id
            ) as mock_new,
            unittest.mock.patch("terok.cli.commands.task.task_login") as mock_task_login,
            unittest.mock.patch(runner_path) as mock_runner,
        ):
            run_main(argv)
        project_name, expected_task_id, kwargs = expected_call
        mock_new.assert_called_once_with(project_name, name=None)
        mock_runner.assert_called_once_with(project_name, expected_task_id, **kwargs)
        # --no-attach must suppress the login exec in every interactive mode.
        mock_task_login.assert_not_called()

    @pytest.mark.parametrize(
        ("argv", "patch_target", "expected_call"),
        [
            (
                ["terok", "project", "init", "myproj"],
                "terok.cli.commands.project.cmd_project_init",
                ("myproj",),
            ),
            (
                ["terok", "login", "proj1", "1"],
                "terok.cli.commands.task.task_login",
                ("proj1", "1"),
            ),
        ],
        ids=["project-init-dispatch", "login-dispatch"],
    )
    def test_simple_dispatch_commands(
        self,
        argv: list[str],
        patch_target: str,
        expected_call: tuple[str, ...],
    ) -> None:
        with (
            unittest.mock.patch(
                "terok.cli.commands.task.resolve_task_id", side_effect=lambda _pid, tid: tid
            ),
            unittest.mock.patch(patch_target) as mock_fn,
        ):
            run_main(argv)
        mock_fn.assert_called_once_with(*expected_call)
