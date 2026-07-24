# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for task_runners internal helpers and the _run_container delegation.

Covers the utility functions (_str_to_bool, _podman_start, _apply_shield_policy)
and the RunSpec delegation path through _run_container.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from terok_executor import BuildError

from terok.lib.orchestration.task_runners.config import _apply_unrestricted_env, _str_to_bool
from terok.lib.orchestration.task_runners.container import _run_container
from tests.test_utils import captured_runspec
from tests.testfs import MOCK_TASK_DIR

# ── _str_to_bool ─────────────────────────────────────────


class TestStrToBool:
    """Verify strict config-value coercion."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("yes", True),
            ("1", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("off", False),
            ("OFF", False),
            (1, True),
            (0, False),
        ],
        ids=[
            "bool-true",
            "bool-false",
            "str-true",
            "str-True",
            "str-yes",
            "str-1",
            "str-false",
            "str-False",
            "str-0",
            "str-no",
            "str-off",
            "str-OFF",
            "int-1",
            "int-0",
        ],
    )
    def test_coercion(self, value: object, expected: bool) -> None:
        """Each value coerces to the expected boolean."""
        assert _str_to_bool(value) is expected


# ── _podman_start ─────────────────────────────────────────


class TestPodmanStart:
    """Verify _podman_start error handling."""

    def test_raises_on_missing_podman(self, mock_runtime) -> None:
        """FileNotFoundError becomes SystemExit with install hint."""
        from terok.lib.orchestration.task_runners.container import _podman_start

        mock_runtime.container.return_value.start.side_effect = FileNotFoundError
        with pytest.raises(SystemExit, match="podman not found"):
            _podman_start(Mock(), "test-ctr")

    def test_raises_on_start_failure(self, mock_runtime) -> None:
        """Runtime failure surfaces as a user-facing SystemExit."""
        from terok.lib.orchestration.task_runners.container import _podman_start

        mock_runtime.container.return_value.start.side_effect = RuntimeError("container not found")
        with pytest.raises(SystemExit, match="container not found"):
            _podman_start(Mock(), "test-ctr")

    def test_raises_on_start_failure_empty_stderr(self, mock_runtime) -> None:
        """Any RuntimeError from the runtime is translated to SystemExit."""
        from terok.lib.orchestration.task_runners.container import _podman_start

        mock_runtime.container.return_value.start.side_effect = RuntimeError("rc=125")
        with pytest.raises(SystemExit):
            _podman_start(Mock(), "test-ctr")

    def test_ensures_runtime_dir_before_start(self, mock_runtime) -> None:
        """The /run/terok bind source is rebuilt (mode 0700) before start.

        After a host reboot the per-container runtime dir is wiped; without
        recreating it ``podman start`` fails on the missing mount source.
        The dir must exist *at* start time, so the check rides the mocked
        start call.
        """
        from terok.lib.core.config import make_sandbox_config
        from terok.lib.orchestration.task_runners.container import _podman_start

        cname = "terok-cli-reboot"
        run_dir = make_sandbox_config().runtime_dir / "run" / cname
        assert not run_dir.exists()

        def _check_dir_ready() -> None:
            assert run_dir.is_dir(), "runtime dir must exist before podman start"
            assert (run_dir.stat().st_mode & 0o777) == 0o700

        mock_runtime.container.return_value.start.side_effect = _check_dir_ready
        _podman_start(Mock(), cname)
        mock_runtime.container.return_value.start.assert_called_once()


# ── _apply_shield_policy ─────────────────────────────────


class TestApplyShieldPolicy:
    """Verify shield policy logic for creation and restart."""

    def _make_project(self, *, drop: bool = True, on_restart: str = "retain") -> MagicMock:
        """Return a mock ProjectConfig with shield fields set."""
        p = MagicMock()
        p.shield_drop_on_task_run = drop
        p.shield_on_task_restart = on_restart
        return p

    def test_fresh_skips_when_drop_disabled(self, tmp_path: Path) -> None:
        """No shield_down call when drop_on_task_run is False."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(drop=False)
        with patch(
            "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
            return_value=False,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        assert (tmp_path / "shield_desired_state").read_text().strip() == "up"

    def test_fresh_drops_and_persists(self, tmp_path: Path) -> None:
        """Fresh creation with drop=True calls shield_down and writes state."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(drop=True)
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.shield.ShieldManager") as mock_down,
            patch(
                "terok.lib.orchestration.task_runners.shield.resolve_container_uuid",
                return_value="cafef00d",
            ),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        mock_down.return_value.down.assert_called_once_with("ctr", "cafef00d")
        assert (tmp_path / "shield_desired_state").read_text().strip() == "down"

    def test_skips_when_bypass_active(self) -> None:
        """No-op when shield bypass is globally active."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(drop=True)
        with patch(
            "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
            return_value=True,
        ):
            _apply_shield_policy(project, "ctr", MOCK_TASK_DIR, is_restart=False)

    def test_restart_retain_restores_down(self, tmp_path: Path) -> None:
        """Restart with retain policy restores a saved 'down' state."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.shield.ShieldManager") as mock_down,
            patch(
                "terok.lib.orchestration.task_runners.shield.resolve_container_uuid",
                return_value="cafef00d",
            ),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.return_value.down.assert_called_once_with("ctr", "cafef00d", allow_all=False)

    def test_restart_retain_restores_disengaged(self, tmp_path: Path) -> None:
        """Restart with retain policy restores a saved 'disengaged' state."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("disengaged\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.shield.ShieldManager") as mock_down,
            patch(
                "terok.lib.orchestration.task_runners.shield.resolve_container_uuid",
                return_value="cafef00d",
            ),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.return_value.down.assert_called_once_with("ctr", "cafef00d", allow_all=True)

    def test_restart_retain_noop_when_up(self, tmp_path: Path) -> None:
        """Restart with retain + saved 'up' does nothing (hook already applied UP)."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("up\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.shield.ShieldManager") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.return_value.down.assert_not_called()

    def test_corrupt_desired_state_raises(self, tmp_path: Path) -> None:
        """A truncated/corrupt shield-state file must raise — silently
        falling back to the OCI-hook default would flip the operator's
        last persisted policy without any diagnostic."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("do")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            pytest.raises(ValueError, match="corrupt shield-state file"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)

    def test_restart_up_policy_noop(self, tmp_path: Path) -> None:
        """Restart with 'up' policy never calls shield_down."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down\n")
        project = self._make_project(on_restart="up")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.shield.ShieldManager") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.return_value.down.assert_not_called()

    def test_drop_failure_is_best_effort(self, tmp_path: Path) -> None:
        """A failed drop warns but does not propagate — it is best-effort and
        re-attempts on the next restart; the ``down`` intent still persists."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(drop=True)
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch(
                "terok.lib.orchestration.task_runners.shield.ShieldManager",
                side_effect=RuntimeError("nft missing"),
            ),
            pytest.warns(match="shield drop"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        # Intent persists even when the drop failed — restart will retry.
        assert (tmp_path / "shield_desired_state").read_text() == "down\n"

    def test_post_start_failure_stops_container_and_reraises(
        self, mock_runtime: MagicMock, tmp_path: Path
    ) -> None:
        """An unexpected shield-application failure stops the live container
        before re-raising — a half-protected, untracked container must never be
        left running."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(drop=True)
        project.shutdown_timeout = 7
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch(
                "terok.lib.orchestration.task_runners.shield._drop_shield_on_creation",
                side_effect=RuntimeError("nft missing"),
            ),
            pytest.raises(RuntimeError, match="nft missing"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        mock_runtime.container.assert_any_call("ctr")
        mock_runtime.container.return_value.stop.assert_called_once_with(timeout=7)

    def test_stop_failure_does_not_mask_shield_error(
        self, mock_runtime: MagicMock, tmp_path: Path
    ) -> None:
        """The best-effort stop swallows its own errors so the original
        shield failure stays the surface error."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(drop=True)
        mock_runtime.container.return_value.stop.side_effect = RuntimeError("podman gone")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch(
                "terok.lib.orchestration.task_runners.shield._drop_shield_on_creation",
                side_effect=RuntimeError("nft missing"),
            ),
            pytest.raises(RuntimeError, match="nft missing"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=False)
        mock_runtime.container.return_value.stop.assert_called_once()

    def test_restart_retain_noop_when_no_file(self, tmp_path: Path) -> None:
        """Restart with retain + no persisted state file does nothing."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.orchestration.task_runners.shield.ShieldManager") as mock_down,
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)
        mock_down.return_value.down.assert_not_called()

    def test_restart_retain_warns_on_restore_failure(self, tmp_path: Path) -> None:
        """Restart with retain warns on restore failure but does not propagate —
        restore is best-effort, like the drop."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        (tmp_path / "shield_desired_state").write_text("down\n")
        project = self._make_project(on_restart="retain")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            patch(
                "terok.lib.orchestration.task_runners.shield.ShieldManager",
                side_effect=RuntimeError("nft not found"),
            ),
            pytest.warns(match="shield restore"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)

    def test_restart_unknown_policy_raises(self, tmp_path: Path) -> None:
        """Unknown on_task_restart value raises ValueError."""
        from terok.lib.orchestration.task_runners.shield import _apply_shield_policy

        project = self._make_project(on_restart="bogus")
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
            pytest.raises(ValueError, match="Unknown shield.on_task_restart"),
        ):
            _apply_shield_policy(project, "ctr", tmp_path, is_restart=True)


# ── _compose_shield_tiers ─────────────────────────────────


def test_compose_shield_tiers_authors_t40_and_t10() -> None:
    """t40 = git host + custom allow; t10 = active overrides (expired dropped)."""
    from datetime import date, timedelta
    from types import SimpleNamespace

    from terok.lib.core.project_model import ShieldOverride
    from terok.lib.orchestration.task_runners.container import _compose_shield_tiers

    future = (date.today() + timedelta(days=1)).isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()
    project = SimpleNamespace(
        upstream_url="https://github.com/foo/bar.git",
        shield_allow=("pypi.org",),
        shield_override=(
            ShieldOverride(host="api.debug.example", reason="debug"),
            ShieldOverride(host="api.active.example", reason="soon", expires=future),
            ShieldOverride(host="api.expired.example", reason="old", expires=past),
        ),
    )

    project_allow, override = _compose_shield_tiers(project)

    assert project_allow == ("github.com", "pypi.org")  # git remote host + custom allow
    assert override == ("api.debug.example", "api.active.example")  # expired dropped


def test_compose_shield_tiers_empty_without_upstream() -> None:
    """No upstream + no config yields empty tiers (nothing authored)."""
    from types import SimpleNamespace

    from terok.lib.orchestration.task_runners.container import _compose_shield_tiers

    project = SimpleNamespace(upstream_url=None, shield_allow=(), shield_override=())
    assert _compose_shield_tiers(project) == ((), ())


# ── _run_container ────────────────────────────────────────


class TestRunContainer:
    """Verify _run_container builds a correct RunSpec and delegates."""

    def _make_project(self) -> MagicMock:
        """Return a mock ProjectConfig for _run_container."""
        from terok.lib.core.project_model import ProjectConfig

        p = MagicMock(spec=ProjectConfig)
        p.name = "p1"
        p.gpus = None
        p.root = MOCK_TASK_DIR
        p.isolation = "shared"
        p.is_sealed = False
        p.memory = None
        p.cpus = None
        p.nested_containers = False
        p.perf = False
        p.podman_args = []
        p.runtime = None
        p.upstream_url = None
        p.shield_allow = ()
        p.shield_override = ()
        return p

    def test_builds_runspec_and_delegates(self) -> None:
        """_run_container constructs a RunSpec and calls sandbox.run()."""
        from terok_sandbox import VolumeSpec

        vol = VolumeSpec(Path("/a"), "/b")
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="test-ctr",
                image="alpine:latest",
                env={"FOO": "bar"},
                volumes=[vol],
                project=project,
                task_dir=MOCK_TASK_DIR,
                command=["bash", "-lc", "echo hi"],
            )

        sandbox_factory.return_value.launch_prepared.assert_called_once()
        spec = captured_runspec(sandbox_factory)
        assert spec.container_name == "test-ctr"
        assert spec.image == "alpine:latest"
        assert spec.env == {"FOO": "bar"}
        assert spec.volumes == (vol,)
        assert spec.command == ("bash", "-lc", "echo hi")
        assert spec.task_dir == MOCK_TASK_DIR
        assert spec.gpus is None
        assert spec.unrestricted is False  # FOO doesn't have TEROK_UNRESTRICTED

    def test_allow_debugger_forwarded_to_launch_prepared(self) -> None:
        """``allow_debugger`` reaches ``launch_prepared``; default is False."""
        project = self._make_project()
        for requested, expected in ((True, True), (False, False)):
            with patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory:
                _run_container(
                    task_id="t1",
                    cname="test-ctr",
                    image="alpine:latest",
                    env={},
                    volumes=[],
                    project=project,
                    task_dir=MOCK_TASK_DIR,
                    allow_debugger=requested,
                )
            kwargs = sandbox_factory.return_value.launch_prepared.call_args.kwargs
            assert kwargs["allow_debugger"] is expected

    def test_unrestricted_flag_from_env(self) -> None:
        """unrestricted is True when TEROK_UNRESTRICTED is in env."""
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="test-ctr",
                image="alpine:latest",
                env={"TEROK_UNRESTRICTED": "1"},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.unrestricted is True

    def test_gpu_selector_from_project(self) -> None:
        """RunSpec.gpus carries the project's run.gpus selector verbatim."""
        project = self._make_project()
        project.gpus = "all"
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="gpu-ctr",
                image="nvidia:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.gpus == "all"

    def test_extra_args_and_command(self) -> None:
        """extra_args and command are converted to tuples in RunSpec."""
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="ctr",
                image="img:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                extra_args=["-p", "8080:80"],
                command=["bash", "-lc", "toad --serve"],
            )

        spec = captured_runspec(sandbox_factory)
        # _run_container emits the dossier annotation through the typed
        # ``annotations=`` kwarg (sandbox validates against
        # SAFE_ANNOTATION_KEYS).  Caller-supplied extras land in
        # ``extra_args`` untouched.  The file at the annotated path *is*
        # the wire dossier (wire-shape JSON, ``{project, task, name}``);
        # the shield reader rereads it on every emit so renames surface
        # live.
        from terok.lib.orchestration.tasks import dossier_path, tasks_meta_dir

        expected_dossier_path = dossier_path(tasks_meta_dir("p1"), "t1")
        assert spec.annotations["dossier.meta_path"] == str(expected_dossier_path)
        assert spec.extra_args == ("-p", "8080:80")
        assert spec.command == ("bash", "-lc", "toad --serve")

    def test_resource_limits_from_project(self) -> None:
        """memory and cpus flow from ProjectConfig to RunSpec."""
        project = self._make_project()
        project.memory = "4g"
        project.cpus = "2.0"
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="rl-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.memory == "4g"
        assert spec.cpus == "2.0"

    def test_resource_limits_default_none(self) -> None:
        """Resource limits are None when project has no limits set."""
        project = self._make_project()
        project.memory = None
        project.cpus = None
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.memory is None
        assert spec.cpus is None

    def test_krun_cpus_annotation_emitted_with_rounding(self) -> None:
        """Under krun, ``run.cpus`` also rides on the ``krun.cpus`` annotation
        — the standard ``--cpus`` flag only sets the cgroup quota; crun-krun
        ignores it for vCPU sizing.  Fractional cpus round up to whole vCPUs."""
        project = self._make_project()
        project.runtime = "krun"
        project.cpus = "2.5"
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.container._project_runtime_flags",
                return_value=[],  # bypass krun port-reservation etc.
            ),
        ):
            _run_container(
                task_id="t1",
                cname="krun-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.annotations["krun.cpus"] == "3"  # ceil(2.5)

    def test_krun_cpus_annotation_skipped_when_unset(self) -> None:
        """No ``krun.cpus`` annotation when ``run.cpus`` is unset — crun-krun
        falls back to host CPU affinity, which is the historical default."""
        project = self._make_project()
        project.runtime = "krun"
        project.cpus = None
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.container._project_runtime_flags",
                return_value=[],
            ),
        ):
            _run_container(
                task_id="t1",
                cname="krun-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert "krun.cpus" not in spec.annotations

    def test_krun_cpus_annotation_not_emitted_under_crun(self) -> None:
        """``krun.cpus`` is krun-specific — under crun (or runtime=None),
        the annotation must not appear regardless of ``run.cpus``."""
        project = self._make_project()
        project.runtime = None  # crun default
        project.cpus = "2"
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="crun-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert "krun.cpus" not in spec.annotations

    def test_launch_build_error_becomes_system_exit(self) -> None:
        """BuildError from AgentRunner.launch_prepared() is surfaced as SystemExit.

        AgentRunner translates GpuConfigError from the sandbox into BuildError
        so terok's orchestration layer sees one failure type for a failed
        container launch.
        """
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
        ):
            sandbox_factory.return_value.launch_prepared.side_effect = BuildError("CDI broken")
            with pytest.raises(SystemExit, match="CDI broken"):
                _run_container(
                    task_id="t1",
                    cname="gpu-ctr",
                    image="nvidia:latest",
                    env={},
                    volumes=[],
                    project=project,
                    task_dir=MOCK_TASK_DIR,
                )

    def test_missing_podman_becomes_system_exit(self) -> None:
        """FileNotFoundError from the executor boundary surfaces as a
        user-friendly SystemExit — matching the pattern in _podman_start,
        so any path through the sandbox that lets FileNotFoundError leak
        is caught here instead of crashing with a bare traceback.
        """
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
        ):
            sandbox_factory.return_value.launch_prepared.side_effect = FileNotFoundError(
                "[Errno 2] No such file or directory: 'podman'"
            )
            with pytest.raises(SystemExit, match="podman not found"):
                _run_container(
                    task_id="t1",
                    cname="ctr",
                    image="img",
                    env={},
                    volumes=[],
                    project=project,
                    task_dir=MOCK_TASK_DIR,
                )

    def test_hooks_forwarded(self) -> None:
        """LifecycleHooks are passed through to sandbox.run()."""
        from terok_sandbox import LifecycleHooks

        hooks = LifecycleHooks(pre_start=lambda: None)
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
        ):
            _run_container(
                task_id="t1",
                cname="ctr",
                image="img",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                hooks=hooks,
            )

        assert sandbox_factory.return_value.launch_prepared.call_args.kwargs["hooks"] is hooks

    def test_none_command_becomes_empty_tuple(self) -> None:
        """command=None results in an empty tuple in the RunSpec."""
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_bypass_firewall_no_protection",
                return_value=False,
            ),
        ):
            _run_container(
                task_id="t1",
                cname="ctr",
                image="img",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                command=None,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.command == ()

    def test_sealed_flag_propagated(self) -> None:
        """sealed=True when project.is_sealed is True."""
        project = self._make_project()
        project.isolation = "sealed"
        project.is_sealed = True

        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="sealed-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.sealed is True

    def test_shared_flag_default(self) -> None:
        """sealed=False when project uses default shared isolation."""
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="shared-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert spec.sealed is False

    def test_nested_containers_adds_selinux_and_fuse_flags(self) -> None:
        """run.nested_containers=true appends label=nested + /dev/fuse."""
        project = self._make_project()
        project.nested_containers = True
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="nested-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                extra_args=["-p", "127.0.0.1:8080:8080"],
            )

        spec = captured_runspec(sandbox_factory)
        # Caller-supplied flags come first, project-derived flags append.
        assert "--security-opt" in spec.extra_args
        assert "label=nested" in spec.extra_args
        assert "--device" in spec.extra_args
        assert "/dev/fuse" in spec.extra_args
        assert "-p" in spec.extra_args
        assert "127.0.0.1:8080:8080" in spec.extra_args

    def test_nested_containers_default_adds_nothing(self) -> None:
        """run.nested_containers=false (default) leaves extra_args untouched."""
        project = self._make_project()  # nested_containers defaults False
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="plain-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        spec = captured_runspec(sandbox_factory)
        assert "label=nested" not in spec.extra_args
        assert "/dev/fuse" not in spec.extra_args

    def test_perf_grants_perfmon_cap(self) -> None:
        """run.perf=true rides executor's typed caps channel, not extra_args."""
        project = self._make_project()
        project.perf = True
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.container._maybe_warn_perf_restricted"
            ) as warn,
        ):
            _run_container(
                task_id="t1",
                cname="perf-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        warn.assert_called_once()
        spec = captured_runspec(sandbox_factory)
        assert spec.caps == ("perfmon",)
        assert "--cap-add" not in spec.extra_args

    def test_perf_default_grants_nothing(self) -> None:
        """run.perf=false (default) passes no caps and skips the sysctl probe."""
        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            patch(
                "terok.lib.orchestration.task_runners.container._maybe_warn_perf_restricted"
            ) as warn,
        ):
            _run_container(
                task_id="t1",
                cname="plain-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )

        warn.assert_not_called()
        spec = captured_runspec(sandbox_factory)
        assert spec.caps == ()

    def test_podman_args_appended_to_extra_args(self) -> None:
        """run.podman_args land verbatim after caller-supplied extra_args."""
        project = self._make_project()
        project.podman_args = ["-e", "HTTPS_PROXY=http://host:8118", "--shm-size=2g"]
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="t1",
                cname="proxy-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                extra_args=["-p", "127.0.0.1:8080:8080"],
            )

        spec = captured_runspec(sandbox_factory)
        p_idx = spec.extra_args.index("-p")
        e_idx = spec.extra_args.index("-e")
        assert p_idx < e_idx  # caller flags first, project passthrough appends
        assert "HTTPS_PROXY=http://host:8118" in spec.extra_args
        assert "--shm-size=2g" in spec.extra_args

    def test_podman_args_managed_flag_refused_at_launch(self) -> None:
        """The launch path re-runs sandbox's gate on programmatic configs."""
        project = self._make_project()
        project.podman_args = ["--cap-add", "perfmon"]
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
            pytest.raises(SystemExit, match="--cap-add"),
        ):
            _run_container(
                task_id="t1",
                cname="bad-ctr",
                image="alpine:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
            )
        sandbox_factory.return_value.launch_prepared.assert_not_called()


# ── _maybe_warn_perf_restricted ───────────────────────────


class TestPerfParanoidWarning:
    """Verify the warn-not-fail posture of the perf sysctl preflight."""

    def _run_with_sysctl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None
    ) -> None:
        from terok.lib.orchestration.task_runners import container as mod

        sysctl = tmp_path / "perf_event_paranoid"
        if content is not None:
            sysctl.write_text(content)
        monkeypatch.setattr(mod, "PERF_EVENT_PARANOID_PATH", str(sysctl))
        mod._maybe_warn_perf_restricted()

    def test_warns_when_paranoid_blocks_perf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A hardened host (paranoid > 2) earns a warning naming the fix."""
        self._run_with_sysctl(tmp_path, monkeypatch, "4\n")
        out = capsys.readouterr().out
        assert "perf_event_paranoid=4" in out
        assert "sysctl kernel.perf_event_paranoid=2" in out

    def test_silent_when_sysctl_usable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A usable sysctl (≤ 2) produces no output at all."""
        self._run_with_sysctl(tmp_path, monkeypatch, "2\n")
        assert capsys.readouterr().out == ""

    def test_silent_when_sysctl_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Missing sysctl (non-Linux, sandboxed CI) is not a misconfiguration."""
        self._run_with_sysctl(tmp_path, monkeypatch, None)
        assert capsys.readouterr().out == ""


# ── _apply_unrestricted_env ───────────────────────────────


class TestApplyUnrestrictedEnv:
    """Verify unrestricted env injection."""

    def test_sets_flag_and_auto_approve(self) -> None:
        """Injects TEROK_UNRESTRICTED and all agent auto-approve vars."""
        from terok_executor import AgentRoster

        env: dict[str, str] = {}
        _apply_unrestricted_env(env)

        assert env["TEROK_UNRESTRICTED"] == "1"
        # Every canonical auto-approve key from the registry must be present
        expected = AgentRoster.shared().collect_all_auto_approve_env()
        for key, value in expected.items():
            assert env[key] == value, f"missing or wrong auto-approve key {key}"


# ── resolve_container_uuid ────────────────────────────────


class TestResolveContainerUuid:
    """``resolve_container_uuid`` wraps ``podman inspect -f '{{.Id}}'``."""

    def test_returns_inspected_id(self) -> None:
        """A live container yields the full UUID."""
        from terok.lib.orchestration.task_runners.shield import resolve_container_uuid

        with patch(
            "terok.lib.orchestration.task_runners.shield.subprocess.check_output",
            return_value="0123456789abcdef\n",
        ):
            assert resolve_container_uuid("my-task") == "0123456789abcdef"

    def test_raises_on_missing_container(self) -> None:
        """A failed ``podman inspect`` surfaces as ``RuntimeError`` (no swallow)."""
        from subprocess import CalledProcessError

        from terok.lib.orchestration.task_runners.shield import resolve_container_uuid

        with patch(
            "terok.lib.orchestration.task_runners.shield.subprocess.check_output",
            side_effect=CalledProcessError(125, ["podman"], stderr="no such container"),
        ):
            with pytest.raises(RuntimeError, match="podman inspect failed"):
                resolve_container_uuid("gone")

    def test_raises_on_empty_id(self) -> None:
        """Empty stdout is treated as a probe failure."""
        from terok.lib.orchestration.task_runners.shield import resolve_container_uuid

        with patch(
            "terok.lib.orchestration.task_runners.shield.subprocess.check_output",
            return_value="\n",
        ):
            with pytest.raises(RuntimeError, match="empty Id"):
                resolve_container_uuid("empty")


# ── Identity plumbing into launch_prepared ────────────────


class TestLaunchPreparedIdentity:
    """``_run_container`` threads project/task identity into the sidecar.

    The supervisor reads the sidecar JSON at OCI prestart hook time, so
    every ``podman run`` issued by terok must carry ``project_name`` /
    ``task_id`` / ``dossier_path`` for the supervisor to scope its
    state correctly.
    """

    def _make_project(self) -> MagicMock:
        from terok.lib.core.project_model import ProjectConfig

        p = MagicMock(spec=ProjectConfig)
        p.name = "proj-id-x"
        p.gpus = None
        p.root = MOCK_TASK_DIR
        p.isolation = "shared"
        p.is_sealed = False
        p.memory = None
        p.cpus = None
        p.nested_containers = False
        p.perf = False
        p.podman_args = []
        p.runtime = None
        p.upstream_url = None
        p.shield_allow = ()
        p.shield_override = ()
        return p

    def test_identity_kwargs_passed_through(self) -> None:
        """``launch_prepared`` receives ``project_name``, ``task_id``, ``dossier_path``."""
        from terok.lib.orchestration.tasks import dossier_path, tasks_meta_dir

        project = self._make_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner"
            ) as sandbox_factory,
        ):
            _run_container(
                task_id="task-id-y",
                cname="proj-id-x-cli-y",
                image="img:latest",
                env={},
                volumes=[],
                project=project,
                task_dir=MOCK_TASK_DIR,
                command=["true"],
            )

        kwargs = sandbox_factory.return_value.launch_prepared.call_args.kwargs
        assert (
            kwargs["project_id"] == "proj-id-x"
        )  # executor API kwarg name (value is the project name)
        assert kwargs["task_id"] == "task-id-y"
        expected_dossier = dossier_path(tasks_meta_dir("proj-id-x"), "task-id-y")
        assert kwargs["dossier_path"] == expected_dossier
