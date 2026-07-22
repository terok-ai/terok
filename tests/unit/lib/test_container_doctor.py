# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for in-container health check orchestration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import ExecResult
from terok_sandbox.doctor import CheckVerdict, DoctorCheck

from terok.lib.orchestration.container_doctor import (
    _exec_in_container,
    _gate_token_check,
    _git_identity_check,
    _git_remote_check,
    _port_drift_check,
    _read_desired_shield_state,
    run_container_doctor,
)
from tests.testfs import MOCK_BASE

MOCK_TASK_DIR = MOCK_BASE / "projects" / "proj" / "tasks" / "42"


class TestExecInContainer:
    """Low-level sandbox exec helper."""

    def test_delegates_to_runtime_exec(self, mock_runtime) -> None:
        """Threads the caller-supplied runtime through to ``runtime.exec``.

        The runtime is passed in explicitly (rather than resolved
        inside the helper) so the doctor can resolve once per
        ``run_container_doctor`` invocation and reuse the same handle
        across every probe + fix — and so tests can hand in a mock.
        """
        mock_runtime.exec.return_value = ExecResult(exit_code=0, stdout="ok\n", stderr="")
        result = _exec_in_container(mock_runtime, "proj-cli-42", ["echo", "hello"])
        assert result.exit_code == 0
        mock_runtime.container.assert_any_call("proj-cli-42")
        mock_runtime.exec.assert_called_once()
        args, kwargs = mock_runtime.exec.call_args
        assert args[1] == ["echo", "hello"]
        assert kwargs == {"timeout": 10}


class TestGitIdentityCheck:
    """Git identity field verification."""

    def test_ok_when_matching(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        verdict = check.evaluate(0, "Dev User\n", "")
        assert verdict.severity == "ok"

    def test_warn_when_mismatched(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        verdict = check.evaluate(0, "Wrong Name\n", "")
        assert verdict.severity == "warn"
        assert verdict.fixable is True

    def test_warn_when_unset(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        verdict = check.evaluate(1, "", "")
        assert verdict.severity == "warn"

    def test_email_field(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "email")
        verdict = check.evaluate(0, "dev@example.com\n", "")
        assert verdict.severity == "ok"

    def test_email_mismatch(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "email")
        verdict = check.evaluate(0, "wrong@example.com\n", "")
        assert verdict.severity == "warn"
        assert verdict.fixable is True

    def test_has_fix_cmd(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        assert check.fix_cmd is not None
        assert "Dev User" in check.fix_cmd


class TestGitRemoteCheck:
    """Git remote URL verification.

    The gate now runs inside the per-container supervisor and the
    container reaches it via the fixed localhost socat bridge on port
    9418 in both socket and TCP modes — so the expected gatekeeping
    origin is always ``http://<token>@localhost:9418/<repo>``.
    """

    def test_ok_for_gate_url(self) -> None:
        check = _git_remote_check("gatekeeping")
        verdict = check.evaluate(0, "http://abc123@localhost:9418/proj.git\n", "")
        assert verdict.severity == "ok"

    def test_error_when_gate_bypassed(self) -> None:
        check = _git_remote_check("gatekeeping")
        verdict = check.evaluate(0, "git@github.com:org/repo.git\n", "")
        assert verdict.severity == "error"

    def test_error_when_host_containers_internal(self) -> None:
        # The old host-daemon URL shape is now a bypass — the in-container
        # bridge listens on localhost, not host.containers.internal.
        check = _git_remote_check("gatekeeping")
        verdict = check.evaluate(0, "http://abc@host.containers.internal:9418/proj.git\n", "")
        assert verdict.severity == "error"

    def test_ok_for_online_any_url(self) -> None:
        check = _git_remote_check("online")
        verdict = check.evaluate(0, "git@github.com:org/repo.git\n", "")
        assert verdict.severity == "ok"

    def test_warn_when_no_remote(self) -> None:
        check = _git_remote_check("gatekeeping")
        verdict = check.evaluate(1, "", "fatal: no remote")
        assert verdict.severity == "warn"

    def test_error_when_port_mismatch(self) -> None:
        check = _git_remote_check("gatekeeping")
        verdict = check.evaluate(0, "http://abc@localhost:5555/proj.git\n", "")
        assert verdict.severity == "error"
        assert "5555" in verdict.detail
        assert "re-allocated" in verdict.detail


class TestGateTokenCheck:
    """Gate-token chain audit: task meta → container env → workspace remote.

    Probe output shape: first line is ``$TEROK_GATE_TOKEN`` (empty when
    unset), second line the token-bearing remote's URL.  Token values
    must never leak into verdict messages.
    """

    def test_ok_when_full_chain_agrees(self) -> None:
        check = _gate_token_check("gatekeeping", "terok-g-aa")
        verdict = check.evaluate(0, "terok-g-aa\nhttp://terok-g-aa@localhost:9418/proj.git\n", "")
        assert verdict.severity == "ok"

    def test_ok_when_gate_not_wired(self) -> None:
        check = _gate_token_check("gatekeeping", None)
        verdict = check.evaluate(0, "\n\n", "")
        assert verdict.severity == "ok"
        assert "not wired" in verdict.detail

    def test_error_and_fixable_when_workspace_token_stale(self) -> None:
        """The incident shape: recreate minted a new token, workspace kept the old."""
        check = _gate_token_check("gatekeeping", "terok-g-new")
        verdict = check.evaluate(0, "terok-g-new\nhttp://terok-g-old@localhost:9418/proj.git\n", "")
        assert verdict.severity == "error"
        assert verdict.fixable is True
        assert "terok-g-old" not in verdict.detail
        assert "terok-g-new" not in verdict.detail

    def test_warn_when_meta_has_no_token(self) -> None:
        """Pre-persistence task: in-container consistent, but no durable record."""
        check = _gate_token_check("gatekeeping", None)
        verdict = check.evaluate(0, "terok-g-aa\nhttp://terok-g-aa@localhost:9418/proj.git\n", "")
        assert verdict.severity == "warn"
        assert "task meta" in verdict.detail

    def test_warn_when_container_behind_meta(self) -> None:
        """A resumed old generation is fine now and reconciles at recreate."""
        check = _gate_token_check("gatekeeping", "terok-g-meta")
        verdict = check.evaluate(0, "terok-g-aa\nhttp://terok-g-aa@localhost:9418/proj.git\n", "")
        assert verdict.severity == "warn"
        assert "recreate" in verdict.detail

    def test_warn_when_remote_missing(self) -> None:
        check = _gate_token_check("gatekeeping", "terok-g-aa")
        verdict = check.evaluate(0, "terok-g-aa\n", "")
        assert verdict.severity == "warn"

    def test_online_mode_audits_gate_remote(self) -> None:
        check = _gate_token_check("online", "terok-g-aa")
        assert "gate" in check.probe_cmd[-1]
        assert "GATE_REMOTE_URL" in check.fix_cmd[-1]
        verdict = check.evaluate(0, "terok-g-aa\nhttp://terok-g-aa@localhost:9418/proj.git\n", "")
        assert verdict.severity == "ok"

    def test_gatekeeping_fix_reasserts_origin_from_env(self) -> None:
        check = _gate_token_check("gatekeeping", "terok-g-aa")
        assert "origin" in check.fix_cmd[-1]
        assert "CODE_REPO" in check.fix_cmd[-1]


class TestPortDriftCheck:
    """Port drift detection for re-allocated ports."""

    def test_ok_when_ports_match(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        assert check.evaluate(0, "18700\n", "").severity == "ok"

    def test_error_when_ports_differ(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        verdict = check.evaluate(0, "18731\n", "")
        assert verdict.severity == "error"
        assert "re-allocated" in verdict.detail

    def test_ok_when_env_not_set(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        assert check.evaluate(1, "", "").severity == "ok"

    def test_warn_when_env_not_numeric(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        assert check.evaluate(0, "not-a-number\n", "").severity == "warn"


class TestReadDesiredShieldState:
    """Shield desired state file reading."""

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert _read_desired_shield_state(tmp_path) is None

    def test_reads_state(self, tmp_path: Path) -> None:
        (tmp_path / "shield_desired_state").write_text("up\n")
        assert _read_desired_shield_state(tmp_path) == "up"

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / "shield_desired_state").write_text("  disengaged  \n")
        assert _read_desired_shield_state(tmp_path) == "disengaged"


class TestRunContainerDoctor:
    """Orchestrator integration tests."""

    @patch("terok.lib.orchestration.tasks.meta.tasks_meta_dir")
    def test_returns_warn_for_missing_metadata(self, mock_meta_dir: MagicMock) -> None:
        mock_meta_dir.return_value = MOCK_BASE / "nonexistent"
        results = run_container_doctor("proj", "99")
        assert len(results) == 1
        assert results[0][0] == "warn"
        assert "metadata not found" in results[0][2]

    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.tasks.meta.tasks_meta_dir")
    def test_returns_warn_for_never_started(
        self, mock_meta_dir: MagicMock, mock_load: MagicMock, tmp_path: Path
    ) -> None:
        (tmp_path / "42_meta.yml").write_text("name: test\n")
        mock_meta_dir.return_value = tmp_path
        mock_load.return_value = ({}, tmp_path / "42_meta.yml")
        results = run_container_doctor("proj", "42")
        assert results[0][0] == "warn"
        assert "never started" in results[0][2]

    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.tasks.meta.tasks_meta_dir")
    def test_skips_non_running(
        self,
        mock_meta_dir: MagicMock,
        mock_load: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        (tmp_path / "42_meta.yml").write_text("mode: cli\nname: test\n")
        mock_meta_dir.return_value = tmp_path
        mock_load.return_value = ({"mode": "cli"}, tmp_path / "42_meta.yml")
        mock_runtime.container.return_value.state = "exited"
        results = run_container_doctor("proj", "42")
        assert results[0][0] == "info"
        assert "not running" in results[0][2]

    @patch("terok.lib.orchestration.container_doctor._exec_in_container")
    @patch("terok.lib.orchestration.container_doctor._terok_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.AgentRoster.doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.sandbox_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.AgentRoster.shared")
    @patch("terok.lib.orchestration.container_doctor._read_desired_shield_state")
    @patch("terok.lib.orchestration.container_doctor.load_project")
    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.tasks.meta.tasks_meta_dir")
    def test_running_container_executes_probes(
        self,
        mock_meta_dir: MagicMock,
        mock_load_meta: MagicMock,
        mock_sandbox_cfg: MagicMock,
        mock_load_project: MagicMock,
        mock_shield_state: MagicMock,
        mock_roster: MagicMock,
        mock_sandbox_checks: MagicMock,
        mock_agent_checks: MagicMock,
        mock_terok_checks: MagicMock,
        mock_exec: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        # Arrange: task metadata exists and container is running
        (tmp_path / "42_meta.yml").write_text("mode: cli\n")
        mock_meta_dir.return_value = tmp_path
        mock_load_meta.return_value = ({"mode": "cli"}, tmp_path / "42_meta.yml")
        mock_runtime.container.return_value.state = "running"
        mock_sandbox_cfg.return_value = MagicMock(token_broker_port=8080, ssh_signer_port=2222)
        mock_shield_state.return_value = None
        mock_roster.return_value = MagicMock()

        fake_project = MagicMock()
        fake_project.tasks_root = MOCK_BASE / "projects" / "proj" / "tasks"
        mock_load_project.return_value = fake_project

        # Create simple container-side checks
        ok_check = DoctorCheck(
            category="network",
            label="Test TCP",
            probe_cmd=["echo", "ok"],
            evaluate=lambda rc, out, err: CheckVerdict("ok", "reachable"),
        )
        mock_sandbox_checks.return_value = [ok_check]
        mock_agent_checks.return_value = []
        mock_terok_checks.return_value = []

        mock_exec.return_value = ExecResult(exit_code=0, stdout="ok\n", stderr="")

        # Act — supervisor-liveness runs first; stub it so this test stays
        # focused on the layered-probe machinery (its own class covers it).
        with patch(
            "terok.lib.orchestration.container_doctor._check_supervisor_alive",
            return_value=[],
        ):
            results = run_container_doctor("proj", "42")

        # Assert — probe was executed and result collected
        assert len(results) >= 1
        assert results[0] == ("ok", "Test TCP", "reachable")
        mock_exec.assert_called_once()

    @patch("terok.lib.orchestration.container_doctor._exec_in_container")
    @patch("terok.lib.orchestration.container_doctor._terok_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.AgentRoster.doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.sandbox_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.AgentRoster.shared")
    @patch("terok.lib.orchestration.container_doctor._read_desired_shield_state")
    @patch("terok.lib.orchestration.container_doctor.load_project")
    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.tasks.meta.tasks_meta_dir")
    def test_fix_application(
        self,
        mock_meta_dir: MagicMock,
        mock_load_meta: MagicMock,
        mock_sandbox_cfg: MagicMock,
        mock_load_project: MagicMock,
        mock_shield_state: MagicMock,
        mock_roster: MagicMock,
        mock_sandbox_checks: MagicMock,
        mock_agent_checks: MagicMock,
        mock_terok_checks: MagicMock,
        mock_exec: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        # Arrange
        (tmp_path / "42_meta.yml").write_text("mode: cli\n")
        mock_meta_dir.return_value = tmp_path
        mock_load_meta.return_value = ({"mode": "cli"}, tmp_path / "42_meta.yml")
        mock_runtime.container.return_value.state = "running"
        mock_sandbox_cfg.return_value = MagicMock(token_broker_port=8080, ssh_signer_port=2222)
        mock_shield_state.return_value = None
        mock_roster.return_value = MagicMock()

        fake_project = MagicMock()
        fake_project.tasks_root = MOCK_BASE / "projects" / "proj" / "tasks"
        mock_load_project.return_value = fake_project

        # A check that fails but is fixable
        fixable_check = DoctorCheck(
            category="git",
            label="Git user.name",
            probe_cmd=["git", "config", "user.name"],
            evaluate=lambda rc, out, err: CheckVerdict(
                "warn", "git user.name: wrong", fixable=True
            ),
            fix_cmd=["git", "config", "user.name", "Correct"],
            fix_description="Set git user.name to 'Correct'.",
        )
        mock_sandbox_checks.return_value = []
        mock_agent_checks.return_value = []
        mock_terok_checks.return_value = [fixable_check]

        # First call is probe (returns mismatch), second is fix (succeeds)
        mock_exec.side_effect = [
            ExecResult(exit_code=0, stdout="Wrong\n", stderr=""),
            ExecResult(exit_code=0, stdout="", stderr=""),
        ]

        # Act — focus on the probe/fix machinery; the per-container
        # reachability pair and supervisor check are exercised by their own tests.
        with (
            patch(
                "terok.lib.orchestration.container_doctor._check_per_container_services",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_supervisor_alive",
                return_value=[],
            ),
        ):
            results = run_container_doctor("proj", "42", fix=True)

        # Assert — probe result + fix result
        assert len(results) == 2
        assert results[0][0] == "warn"
        assert results[1][0] == "ok"
        assert "fix:" in results[1][1]
        assert mock_exec.call_count == 2

    @patch("terok.lib.orchestration.container_doctor._exec_in_container")
    @patch("terok.lib.orchestration.container_doctor._terok_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.AgentRoster.doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.sandbox_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.AgentRoster.shared")
    @patch("terok.lib.orchestration.container_doctor._read_desired_shield_state")
    @patch("terok.lib.orchestration.container_doctor.load_project")
    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.tasks.meta.tasks_meta_dir")
    def test_host_side_non_shield_check_calls_evaluate(
        self,
        mock_meta_dir: MagicMock,
        mock_load_meta: MagicMock,
        mock_sandbox_cfg: MagicMock,
        mock_load_project: MagicMock,
        mock_shield_state: MagicMock,
        mock_roster: MagicMock,
        mock_sandbox_checks: MagicMock,
        mock_agent_checks: MagicMock,
        mock_terok_checks: MagicMock,
        mock_exec: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        # Arrange
        (tmp_path / "42_meta.yml").write_text("mode: cli\n")
        mock_meta_dir.return_value = tmp_path
        mock_load_meta.return_value = ({"mode": "cli"}, tmp_path / "42_meta.yml")
        mock_runtime.container.return_value.state = "running"
        mock_sandbox_cfg.return_value = MagicMock(token_broker_port=8080, ssh_signer_port=2222)
        mock_shield_state.return_value = None
        mock_roster.return_value = MagicMock()

        fake_project = MagicMock()
        fake_project.tasks_root = MOCK_BASE / "projects" / "proj" / "tasks"
        mock_load_project.return_value = fake_project

        # A host-side check outside the special-cased "shield" category —
        # its ``evaluate`` callable is self-contained and the dispatcher
        # must call it (vault-tier passphrase checks fall in this bucket).
        host_check = DoctorCheck(
            category="vault",
            label="Future check",
            probe_cmd=[],
            evaluate=lambda rc, out, err: CheckVerdict("warn", "evaluated locally"),
            host_side=True,
        )
        mock_sandbox_checks.return_value = [host_check]
        mock_agent_checks.return_value = []
        mock_terok_checks.return_value = []

        # Act — focus on host-side dispatch; reachability + supervisor stubbed out.
        with (
            patch(
                "terok.lib.orchestration.container_doctor._check_per_container_services",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_supervisor_alive",
                return_value=[],
            ),
        ):
            results = run_container_doctor("proj", "42")

        # Assert — the host-side ``evaluate`` ran and its verdict reached us
        assert len(results) == 1
        assert results[0] == ("warn", "Future check", "evaluated locally")
        mock_exec.assert_not_called()

    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    def test_collect_all_checks_raises_in_tcp_mode_with_unset_ports(
        self,
        mock_sandbox_cfg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """``_collect_all_checks`` refuses TCP mode without resolved ports.

        In TCP mode the per-task probes need real port numbers — either
        pinned in ``config.yml`` or auto-allocated by the port registry.
        Socket mode is the opposite contract (no TCP ports expected) and
        is covered by ``test_sickbay_collects_checks_in_socket_mode`` in
        ``test_unified_layering_contracts``.
        """
        from terok.lib.orchestration.container_doctor import _collect_all_checks

        mock_sandbox_cfg.return_value = MagicMock(
            services_mode="tcp", gate_port=None, token_broker_port=None, ssh_signer_port=None
        )
        with pytest.raises(SystemExit, match="ports are not all configured"):
            _collect_all_checks("proj", tmp_path)


class TestStreamingGrouping:
    """Verify that the streaming path partitions checks by heading correctly."""

    def test_group_key_maps_credentials_and_tokens(self) -> None:
        """Labels inside known prefixes collapse to their heading; others pass through."""
        from terok.lib.orchestration.container_doctor import _group_key

        cred = DoctorCheck(
            category="mount",
            label="Credential file (claude)",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        phantom = DoctorCheck(
            category="env",
            label="Phantom token (GH_TOKEN)",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        base_url = DoctorCheck(
            category="env",
            label="Base URL (OPENAI_BASE_URL)",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        shield = DoctorCheck(
            category="shield",
            label="Shield state",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        assert _group_key(cred)[0] == "Credential files"
        assert _group_key(phantom)[0] == "Phantom tokens"
        assert _group_key(base_url)[0] == "Base URLs"
        # Shield has no mapping — streams individually
        assert _group_key(shield)[0] is None

    def test_network_category_collapses_disjoint_contributors(
        self,
        mock_runtime,
        tmp_path: Path,
    ) -> None:
        """Network checks from two layers (sandbox + terok) share one heading.

        Without the grouping that partitions *then* emits, a category
        contributed to by non-consecutive layers would produce two
        separate "Port drift" heading lines.
        """
        from io import StringIO

        from terok.lib.orchestration.container_doctor import (
            run_container_doctor,
        )
        from terok.lib.util.check_reporter import CheckReporter

        (tmp_path / "42_meta.yml").write_text("mode: cli\n")

        net_a = DoctorCheck(
            category="network",
            label="Token broker (TCP)",
            probe_cmd=["true"],
            evaluate=lambda *a: CheckVerdict("ok", "reachable"),
        )
        shield_check = DoctorCheck(
            category="shield",
            label="Shield state",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
            host_side=True,
        )
        net_b = DoctorCheck(
            category="network",
            label="Token broker port drift",
            probe_cmd=["true"],
            evaluate=lambda *a: CheckVerdict("ok", "matches"),
        )

        buf = StringIO()
        reporter = CheckReporter(stream=buf)

        with (
            patch(
                "terok.lib.orchestration.container_doctor.sandbox_doctor_checks",
                return_value=[net_a, shield_check],
            ),
            patch(
                "terok.lib.orchestration.container_doctor.AgentRoster.doctor_checks",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._terok_doctor_checks",
                return_value=[net_b],
            ),
            patch(
                "terok.lib.orchestration.tasks.meta.tasks_meta_dir",
                return_value=tmp_path,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_task_meta",
                return_value=({"mode": "cli"}, tmp_path / "42_meta.yml"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor.make_sandbox_config",
                return_value=MagicMock(token_broker_port=8080, ssh_signer_port=2222),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._read_desired_shield_state",
                return_value=None,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_project",
                return_value=MagicMock(tasks_root=tmp_path),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_shield_state",
                return_value=("ok", "Shield state", "not managed"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._exec_in_container",
                return_value=ExecResult(exit_code=0, stdout="", stderr=""),
            ),
        ):
            mock_runtime.container.return_value.state = "running"
            run_container_doctor("proj", "42", reporter=reporter)

        out = buf.getvalue()
        # Single "Port drift" heading, both network checks counted under it.
        assert out.count("Port drift") == 1
        assert "ok (2 checks)" in out
        # Shield state streams individually between the group members (it's
        # the second check), and must still appear with its own line.
        assert "Shield state" in out

    def test_legacy_callers_still_receive_list(
        self,
        mock_runtime,
        tmp_path: Path,
    ) -> None:
        """Calling without a reporter keeps the historical return shape."""
        from terok.lib.orchestration.container_doctor import (
            run_container_doctor,
        )

        (tmp_path / "42_meta.yml").write_text("mode: cli\n")

        only_check = DoctorCheck(
            category="shield",
            label="Shield state",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
            host_side=True,
        )
        with (
            patch(
                "terok.lib.orchestration.container_doctor.sandbox_doctor_checks",
                return_value=[only_check],
            ),
            patch(
                "terok.lib.orchestration.container_doctor.AgentRoster.doctor_checks",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._terok_doctor_checks",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.tasks.meta.tasks_meta_dir",
                return_value=tmp_path,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_task_meta",
                return_value=({"mode": "cli"}, tmp_path / "42_meta.yml"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor.make_sandbox_config",
                return_value=MagicMock(token_broker_port=8080, ssh_signer_port=2222),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._read_desired_shield_state",
                return_value=None,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_project",
                return_value=MagicMock(tasks_root=tmp_path),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_shield_state",
                return_value=("ok", "Shield state", "not managed"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_per_container_services",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_supervisor_alive",
                return_value=[],
            ),
        ):
            mock_runtime.container.return_value.state = "running"
            results = run_container_doctor("proj", "42")

        # Legacy path returns the accumulated list; streaming path would
        # have returned an empty list.
        assert results == [("ok", "Shield state", "not managed")]


class TestPerContainerReachability:
    """Per-container vault + gate reachability checks (host-side, best-effort)."""

    def _cfg(self, mode: str, *, state_dir: Path, runtime_dir: Path) -> MagicMock:
        cfg = MagicMock()
        cfg.services_mode = mode
        cfg.state_dir = state_dir
        cfg.runtime_dir = runtime_dir
        return cfg

    def test_socket_mode_ok_when_sockets_exist(self, tmp_path: Path) -> None:
        from terok.lib.orchestration.container_doctor import _check_per_container_services

        cname = "proj-cli-42"
        run_dir = tmp_path / "rt" / "run" / cname
        run_dir.mkdir(parents=True)
        (run_dir / "vault.sock").touch()
        (run_dir / "gate-server.sock").touch()

        cfg = self._cfg("socket", state_dir=tmp_path / "state", runtime_dir=tmp_path / "rt")
        with patch(
            "terok.lib.orchestration.container_doctor.make_sandbox_config", return_value=cfg
        ):
            results = _check_per_container_services(cname)

        labels = {label: sev for sev, label, _ in results}
        assert labels == {"Vault reachable": "ok", "Gate reachable": "ok"}

    def test_socket_mode_warns_when_socket_missing(self, tmp_path: Path) -> None:
        from terok.lib.orchestration.container_doctor import _check_per_container_services

        cname = "proj-cli-42"
        cfg = self._cfg("socket", state_dir=tmp_path / "state", runtime_dir=tmp_path / "rt")
        with patch(
            "terok.lib.orchestration.container_doctor.make_sandbox_config", return_value=cfg
        ):
            results = _check_per_container_services(cname)

        # Sockets don't exist → both warn, never fatal.
        assert {sev for sev, _, _ in results} == {"warn"}
        assert [label for _, label, _ in results] == ["Vault reachable", "Gate reachable"]

    def test_tcp_mode_reads_sidecar_ports_and_probes(self, tmp_path: Path) -> None:
        import json

        from terok.lib.orchestration.container_doctor import _check_per_container_services

        cname = "proj-cli-42"
        sidecar_dir = tmp_path / "state" / "sidecar"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / f"{cname}.json").write_text(
            json.dumps({"tcp_port": 18800, "gate_port": 18801})
        )

        cfg = self._cfg("tcp", state_dir=tmp_path / "state", runtime_dir=tmp_path / "rt")
        with (
            patch("terok.lib.orchestration.container_doctor.make_sandbox_config", return_value=cfg),
            patch(
                "terok.lib.orchestration.container_doctor._tcp_reachable", return_value=True
            ) as mock_reach,
        ):
            results = _check_per_container_services(cname)

        assert {sev for sev, _, _ in results} == {"ok"}
        mock_reach.assert_any_call(18800)
        mock_reach.assert_any_call(18801)

    def test_tcp_mode_warns_when_unreachable(self, tmp_path: Path) -> None:
        import json

        from terok.lib.orchestration.container_doctor import _check_per_container_services

        cname = "proj-cli-42"
        sidecar_dir = tmp_path / "state" / "sidecar"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / f"{cname}.json").write_text(json.dumps({"tcp_port": 18800}))

        cfg = self._cfg("tcp", state_dir=tmp_path / "state", runtime_dir=tmp_path / "rt")
        with (
            patch("terok.lib.orchestration.container_doctor.make_sandbox_config", return_value=cfg),
            patch("terok.lib.orchestration.container_doctor._tcp_reachable", return_value=False),
        ):
            results = _check_per_container_services(cname)

        sev_by_label = {label: sev for sev, label, _ in results}
        # Vault has a recorded port but is unreachable → warn.
        assert sev_by_label["Vault reachable"] == "warn"
        # Gate has no recorded port (sidecar omits gate_port) → warn, cannot probe.
        assert sev_by_label["Gate reachable"] == "warn"

    def test_missing_sidecar_is_best_effort(self, tmp_path: Path) -> None:
        from terok.lib.orchestration.container_doctor import _read_sidecar_ports

        cfg = self._cfg("tcp", state_dir=tmp_path / "state", runtime_dir=tmp_path / "rt")
        with patch(
            "terok.lib.orchestration.container_doctor.make_sandbox_config", return_value=cfg
        ):
            # No sidecar file written → empty mapping, no exception.
            assert _read_sidecar_ports("proj-cli-42") == {}


class TestSupervisorAliveCheck:
    """Root-cause 'Supervisor alive' check (issue #1189)."""

    @staticmethod
    def _live(alive: bool, pid: int | None, detail: str) -> SimpleNamespace:
        """A stand-in for ``terok_sandbox.SupervisorLiveness``."""
        return SimpleNamespace(alive=alive, pid=pid, detail=detail)

    def test_unresolved_container_id_skips(self) -> None:
        from terok.lib.orchestration.container_doctor import _check_supervisor_alive

        assert _check_supervisor_alive("proj-cli-42", "") == []

    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.supervisor_liveness")
    def test_ok_when_supervisor_alive(
        self, mock_live: MagicMock, mock_cfg: MagicMock, tmp_path: Path
    ) -> None:
        from terok.lib.orchestration.container_doctor import _check_supervisor_alive

        mock_cfg.return_value = MagicMock(state_dir=tmp_path)
        mock_live.return_value = self._live(True, 4242, "supervisor pid 4242 alive")
        assert _check_supervisor_alive("proj-cli-42", "cid123") == [
            ("ok", "Supervisor alive", "supervisor pid 4242 alive")
        ]

    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.supervisor_liveness")
    def test_error_names_cause_and_points_at_logs(
        self, mock_live: MagicMock, mock_cfg: MagicMock, tmp_path: Path
    ) -> None:
        from terok.lib.orchestration.container_doctor import _check_supervisor_alive

        mock_cfg.return_value = MagicMock(state_dir=tmp_path)
        mock_live.return_value = self._live(
            False, None, "no PID file — the supervisor hook never spawned a supervisor"
        )
        status, label, detail = _check_supervisor_alive("proj-cli-42", "cid123")[0]
        assert status == "error"
        assert label == "Supervisor alive"
        assert "no PID file" in detail
        assert "hook.log" in detail  # points at the cross-container diary
        assert "cid123.log" in detail  # and this container's supervisor log


class TestReachabilityReferencesSupervisor:
    """A missing endpoint cites the supervisor check when the supervisor is down."""

    def _cfg(self, tmp_path: Path) -> MagicMock:
        cfg = MagicMock()
        cfg.services_mode = "socket"
        cfg.state_dir = tmp_path / "state"
        cfg.runtime_dir = tmp_path / "rt"
        return cfg

    def test_missing_socket_references_root_cause_when_down(self, tmp_path: Path) -> None:
        from terok.lib.orchestration.container_doctor import _check_per_container_services

        with patch(
            "terok.lib.orchestration.container_doctor.make_sandbox_config",
            return_value=self._cfg(tmp_path),
        ):
            results = _check_per_container_services("proj-cli-42", supervisor_up=False)
        assert all(status == "warn" for status, _, _ in results)
        assert all("supervisor is not running" in detail for _, _, detail in results)

    def test_missing_socket_stays_bare_when_supervisor_up(self, tmp_path: Path) -> None:
        from terok.lib.orchestration.container_doctor import _check_per_container_services

        with patch(
            "terok.lib.orchestration.container_doctor.make_sandbox_config",
            return_value=self._cfg(tmp_path),
        ):
            results = _check_per_container_services("proj-cli-42", supervisor_up=True)
        assert all("supervisor is not running" not in detail for _, _, detail in results)
