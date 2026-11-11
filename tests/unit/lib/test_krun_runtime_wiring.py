# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok's krun wiring: `resolve_runtime` priority chain,
project config plumbing, and the orchestration-owned flags emitted at
task launch (``--runtime krun`` + microVM annotations + port forward).
The image/keypair/sshd-trigger trio is delegated to
[`krun_launch_args`][terok_executor.krun.krun_launch_args] in
terok-executor and tested there; terok only verifies the delegation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.lib.core.runtime import launch_runtime_arg, resolve_runtime, resolve_runtime_name
from terok.lib.integrations.sandbox import KrunRuntime, NullRuntime, PodmanRuntime
from terok.lib.orchestration.task_runners.container import (
    _agent_runner,
    _validate_krun_compatibility,
)
from tests.testfs import MOCK_BASE


def _krun_project(**overrides):  # type: ignore[no-untyped-def]
    """Build a minimal ProjectConfig with ``runtime="krun"`` (override-friendly)."""
    from terok.lib.core.project_model import ProjectConfig

    defaults: dict[str, object] = {
        "name": "p",
        "security_class": "online",
        "upstream_url": None,
        "default_branch": None,
        "root": MOCK_BASE / "p",
        "tasks_root": MOCK_BASE / "p" / "tasks",
        "gate_path": MOCK_BASE / "p" / "gate",
        "staging_root": None,
        "runtime": "krun",
        # ProjectConfig defaults to "socket"; krun needs "tcp", so the
        # factory builds a compatible project — the dedicated guard test
        # overrides back to "socket" to exercise the rejection.
        "services_mode": "tcp",
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)


def _project(**overrides):  # type: ignore[no-untyped-def]
    """Build a minimal ProjectConfig with no runtime set (resolver falls through)."""
    from terok.lib.core.project_model import ProjectConfig

    defaults: dict[str, object] = {
        "name": "demoproj",
        "security_class": "online",
        "upstream_url": None,
        "default_branch": None,
        "root": MOCK_BASE / "demoproj",
        "tasks_root": MOCK_BASE / "demoproj" / "tasks",
        "gate_path": MOCK_BASE / "demoproj" / "gate",
        "staging_root": None,
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)


class TestResolveRuntime:
    """`resolve_runtime(project)` priority chain: env > project > global > crun."""

    def test_no_project_no_env_defaults_to_crun(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The bottom of the fallback chain — every other source unset.

        Patches the global-config reader to return ``None`` (no global
        ``run.runtime`` set) so the resolver's own ``or "crun"`` fallback
        runs.
        """
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
        with patch("terok.lib.core.runtime.get_global_run_runtime", return_value=None):
            assert isinstance(resolve_runtime(None), PodmanRuntime)

    def test_env_overrides_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``TEROK_RUNTIME`` env var wins over project + global config."""
        monkeypatch.setenv("TEROK_RUNTIME", "null")
        # Project says krun, global says krun — env still wins with "null".
        with patch("terok.lib.core.runtime.get_global_run_runtime", return_value="krun"):
            assert isinstance(resolve_runtime(_krun_project()), NullRuntime)

    def test_project_runtime_overrides_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project's ``run.runtime`` beats global ``run.runtime``."""
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
        with patch("terok.lib.core.runtime.get_global_run_runtime", return_value="krun"):
            # Project explicitly picks crun.
            assert isinstance(resolve_runtime(_project(runtime="crun")), PodmanRuntime)

    def test_global_runtime_used_when_project_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No project value → fall through to global default."""
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
        fake_runtime = MagicMock(spec=KrunRuntime)
        with (
            patch("terok.lib.core.runtime.get_global_run_runtime", return_value="krun"),
            patch("terok.lib.core.runtime.is_experimental", return_value=True),
            patch(
                "terok.lib.core.runtime.KrunHost",
                return_value=MagicMock(runtime=MagicMock(return_value=fake_runtime)),
            ) as host_cls,
        ):
            rt = resolve_runtime(_project())  # project.runtime is None
        assert rt is fake_runtime
        host_cls.assert_called_once()

    def test_krun_requires_experimental_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without ``experimental: true``, krun selection is refused loudly."""
        monkeypatch.setenv("TEROK_RUNTIME", "krun")
        with patch("terok.lib.core.runtime.is_experimental", return_value=False):
            with pytest.raises(SystemExit, match="experimental"):
                resolve_runtime(None)

    def test_krun_with_experimental_delegates_to_executor_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Experimental on + runtime=krun routes through ``KrunHost(cfg).runtime()``.

        terok no longer owns the host-key materialisation — it just
        flips the runtime selector and lets executor's
        [`KrunHost`][terok_executor.KrunHost] do the rest.
        """
        monkeypatch.setenv("TEROK_RUNTIME", "krun")
        fake_runtime = MagicMock(spec=KrunRuntime)
        with (
            patch("terok.lib.core.runtime.is_experimental", return_value=True),
            patch(
                "terok.lib.core.runtime.KrunHost",
                return_value=MagicMock(runtime=MagicMock(return_value=fake_runtime)),
            ) as host_cls,
        ):
            rt = resolve_runtime(None)
        assert rt is fake_runtime
        host_cls.assert_called_once()  # cfg= injected from make_sandbox_config()

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_blank_env_override_is_loud_everywhere(
        self, monkeypatch: pytest.MonkeyPatch, blank: str
    ) -> None:
        """An empty ``TEROK_RUNTIME`` never silently picks crun over the project's choice."""
        monkeypatch.setenv("TEROK_RUNTIME", blank)
        with pytest.raises(SystemExit, match="crun.*krun.*null"):
            resolve_runtime_name(_project(runtime="krun"))
        with pytest.raises(SystemExit, match="crun.*krun.*null"):
            launch_runtime_arg(_project(runtime="krun"))

    def test_unknown_value_is_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown ``TEROK_RUNTIME`` value raises SystemExit (no silent fallback)."""
        monkeypatch.setenv("TEROK_RUNTIME", "tomato")
        with pytest.raises(SystemExit, match="crun.*krun.*null"):
            resolve_runtime(None)

    def test_name_normalizes_env_without_constructing_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Runtime selection stays cheap and normalizes the existing env override."""
        monkeypatch.setenv("TEROK_RUNTIME", " KRUN ")
        with patch("terok.lib.core.runtime.KrunHost") as host:
            assert resolve_runtime_name(_project(runtime="crun")) == "krun"
        host.assert_not_called()


class TestKrunCompatibilityGuard:
    """`_validate_krun_compatibility` rejects nested-container combos
    and refuses krun selection without the experimental flag."""

    def test_rejects_nested_containers(self, _experimental_enabled) -> None:
        project = _krun_project(nested_containers=True)
        with pytest.raises(SystemExit, match="incompatible.*nested_containers"):
            _validate_krun_compatibility(project)

    def test_accepts_krun_without_nested(self, _experimental_enabled) -> None:
        project = _krun_project()
        _validate_krun_compatibility(project)  # no raise

    def test_rejects_krun_without_experimental_flag(self) -> None:
        """run.runtime: krun is gated on `experimental: true`.

        The env-var path (``TEROK_RUNTIME=krun``) and the project-config
        path must both refuse to enable the experimental backend without
        the explicit opt-in.  Otherwise a typo in project.yml silently
        switches the workload to less-audited isolation.
        """
        project = _krun_project()
        with (
            patch(
                "terok.lib.orchestration.task_runners.container.is_experimental",
                return_value=False,
            ),
            pytest.raises(SystemExit, match="requires the experimental flag"),
        ):
            _validate_krun_compatibility(project)

    def test_rejects_krun_under_socket_services_mode(self) -> None:
        """run.runtime: krun + services.mode: socket fail-fast with a fix hint.

        The microVM's kernel can't see the host's unix sockets (no
        shared mount namespace), so token-broker / ssh-signer bridges
        silently fail to establish.  Refusing at launch with a clear
        pointer to ``services.mode: tcp`` beats a baffling timeout
        deeper in the bring-up.
        """
        project = _krun_project(services_mode="socket")
        with (
            patch(
                "terok.lib.orchestration.task_runners.container.is_experimental",
                return_value=True,
            ),
            pytest.raises(SystemExit, match="incompatible.*services.mode: socket"),
        ):
            _validate_krun_compatibility(project)


@pytest.fixture()
def _experimental_enabled():
    """Patch the krun guards open for tests that exercise the krun path.

    ``_validate_krun_compatibility`` refuses krun without the
    experimental flag; these tests verify the happy path *after* the
    gate, so it is patched out.  The transport guard needs no patch —
    ``_krun_project`` already builds a ``services_mode="tcp"`` project.
    """
    with patch(
        "terok.lib.orchestration.task_runners.container.is_experimental",
        return_value=True,
    ):
        yield


_LAUNCH_ARGS_STUB = ["__stub_launch_args__"]


@pytest.fixture()
def _krun_launch_args_stub():
    """Patch ``KrunHost(...).launch_args()`` to return a sentinel list.

    Terok's job is to splice executor's helper output into its flag
    stream — the helper's contents (host-pubkey mount, sshd-trigger
    env var, ``--user root``) are pinned on the executor side.
    """
    fake_host = MagicMock()
    fake_host.launch_args = MagicMock(return_value=list(_LAUNCH_ARGS_STUB))
    with patch(
        "terok.lib.orchestration.task_runners.container.KrunHost",
        return_value=fake_host,
    ):
        yield fake_host.launch_args


@pytest.fixture()
def _stub_port_reservation():
    """Patch ``PodmanRuntime.reserve_port`` to hand back a fixed port.

    The real reservation binds an ephemeral TCP socket and releases on
    exit; tests just need the port number to be stable so the assertions
    can spot it in the assembled flag list.
    """

    class _StubReservation:
        port = 42201

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            return None

    with patch("terok.lib.orchestration.task_runners.container.PodmanRuntime") as podman_cls:
        podman_cls.return_value.reserve_port.return_value = _StubReservation()
        yield _StubReservation


class TestProjectRuntimeFlags:
    """`_project_runtime_flags` emits --runtime + sshd port-forward and
    splices in executor's [`krun_launch_args`][terok_executor.krun.krun_launch_args]."""

    def test_no_runtime_no_flags(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        assert _project_runtime_flags(_project(), cname="terok-cli-demoproj-task-a") == []

    def test_krun_does_not_emit_runtime_flag(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        """``--runtime krun`` flows through ``RunSpec.runtime`` (a typed
        channel sandbox already turns into both podman's flag and shield's
        dnsmasq-bind selection), not through this flag list.  Emitting it
        here too would land ``--runtime`` twice on the podman invocation.
        """
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", services_mode="tcp"), cname="terok-cli-demoproj-task-a"
        )
        assert "--runtime" not in flags

    def test_krun_emits_loopback_pinned_port_forward(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        """``-p 127.0.0.1:<reserved>:22`` — the host side of the forward is
        pinned to loopback so pasta doesn't expose the krun task's sshd on
        external interfaces.  No terok-private annotation: podman already
        tracks the mapping; ``podman_port_resolver`` reads it back via
        ``podman port`` at exec time."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", services_mode="tcp"), cname="terok-cli-demoproj-task-a"
        )

        p_idx = flags.index("-p")
        assert flags[p_idx + 1] == "127.0.0.1:42201:22"

        # The previous design carried a terok.krun.port annotation; assert
        # nothing of the sort remains, so a regression that brings it back
        # would also have to delete this assertion.
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        assert not any("terok.krun.port" in a for a in annotations)

    def test_krun_splices_executor_launch_args(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        """The image-side trio (host-pubkey mount, sshd-trigger env var,
        ``--user root``) is owned by executor; terok must delegate and
        splice the result rather than open-code it.
        """
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", services_mode="tcp"), cname="terok-cli-demoproj-task-a"
        )
        _krun_launch_args_stub.assert_called_once()
        for token in _LAUNCH_ARGS_STUB:
            assert token in flags

    def test_non_krun_does_not_call_launch_args(self, _krun_launch_args_stub) -> None:
        """Under any runtime other than krun the executor helper is never
        consulted — its outputs (sshd-trigger env var, ``--user root``,
        host-pubkey mount) belong exclusively to the krun launch path."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        _project_runtime_flags(_project(), cname="terok-cli-demoproj-task-a")
        _krun_launch_args_stub.assert_not_called()

    def test_krun_runtime_flags_dont_carry_annotations(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        """``_project_runtime_flags`` returns *podman flags* only.  Annotations
        (dossier, krun.cpus) flow through the typed ``annotations=`` kwarg
        in ``_run_container``, not through this flag list.  Asserted here so
        the two channels don't quietly merge."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", services_mode="tcp", memory="4g", cpus="2"),
            cname="terok-cli-demoproj-task-a",
        )
        joined = " ".join(flags)
        assert "--annotation" not in joined
        assert "krun.cpus" not in joined
        assert "dossier.meta_path" not in joined

    def test_krun_plus_nested_rejected(self, _experimental_enabled) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        with pytest.raises(SystemExit, match="incompatible.*nested"):
            _project_runtime_flags(
                _project(runtime="krun", nested_containers=True),
                cname="terok-cli-demoproj-task-a",
            )

    def test_krun_without_experimental_rejected(self) -> None:
        """No experimental flag → config-driven krun selection refused."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        with (
            patch(
                "terok.lib.orchestration.task_runners.container.is_experimental",
                return_value=False,
            ),
            pytest.raises(SystemExit, match="requires the experimental flag"),
        ):
            _project_runtime_flags(
                _project(runtime="krun", services_mode="tcp"), cname="terok-cli-demoproj-task-a"
            )

    def test_nested_only_unaffected(self, _krun_launch_args_stub) -> None:
        """`nested_containers` alone still emits its existing flags (no krun bits)."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(nested_containers=True), cname="terok-cli-demoproj-task-a"
        )
        assert "label=nested" in flags
        assert "--runtime" not in flags
        _krun_launch_args_stub.assert_not_called()


class TestKrunDnsTopologyDelegated:
    """The krun DNS topology now lives in terok-shield itself: shield picks
    the dnsmasq bind from a runtime hint that flows through
    ``RunSpec.runtime``.  No post-pre_start workaround anywhere in terok.
    """

    def test_no_chain_krun_dns_rewrite_helper(self) -> None:
        """The old ``_chain_krun_dns_rewrite`` workaround is gone — shield
        owns its own resolv.conf address now."""
        from terok.lib.orchestration.task_runners import container

        assert not hasattr(container, "_chain_krun_dns_rewrite")


@pytest.fixture(
    params=[
        ("krun", "crun", "crun", "krun", "krun"),
        (None, None, "krun", "krun", "krun"),
        ("crun", "krun", "krun", "crun", "crun"),
        (None, "crun", "krun", "crun", "crun"),
        ("null", "krun", "krun", "null", None),
        (None, None, None, "crun", None),
    ],
    ids=["env-krun", "global-krun", "env-crun", "project-crun", "env-null", "unset"],
)
def _runtime_selection(request, monkeypatch: pytest.MonkeyPatch):
    """A (project, factory backend, podman ``--runtime`` argument) triple per precedence case."""
    env_runtime, project_runtime, global_runtime, expected, launch_arg = request.param
    if env_runtime is None:
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
    else:
        monkeypatch.setenv("TEROK_RUNTIME", env_runtime)
    with patch("terok.lib.core.runtime.get_global_run_runtime", return_value=global_runtime):
        yield (
            _project(runtime=project_runtime, services_mode="tcp", cpus="2.5"),
            expected,
            launch_arg,
        )


class TestEffectiveRuntimeSelection:
    """Launch wiring and shield refresh follow env > project > global.

    Podman inherits its own default when nothing selects a runtime: the
    factory still builds the podman backend, but no ``--runtime`` is sent.
    """

    def test_launch_argument_is_the_configured_selection_only(self, _runtime_selection) -> None:
        project, expected, launch_arg = _runtime_selection
        assert resolve_runtime_name(project) == expected
        assert launch_runtime_arg(project) == launch_arg

    def test_launch_uses_one_runtime_for_factory_flags_annotations_and_hint(
        self,
        _runtime_selection,
        _experimental_enabled,
        _krun_launch_args_stub,
        _stub_port_reservation,
        tmp_path,
    ) -> None:
        from terok.lib.integrations.sandbox import BrokerContext
        from terok.lib.orchestration.task_runners.container import _run_container

        project, expected, launch_arg = _runtime_selection
        with (
            # The unit conftest stubs the runner factory; the real one builds the Sandbox.
            patch(
                "terok.lib.orchestration.task_runners.container._agent_runner",
                side_effect=_agent_runner,
            ),
            patch("terok.lib.core.runtime.resolve_runtime") as resolve,
            patch("terok.lib.orchestration.task_runners.container.Sandbox") as sandbox,
            patch("terok.lib.orchestration.task_runners.container.AgentRunner") as runner,
        ):
            _run_container(
                cname="runtime-ctr",
                image="test:latest",
                env={},
                volumes=[],
                project=project,
                task_id="1",
                task_dir=tmp_path,
                broker_context=BrokerContext("test", "1", "default"),
            )

        assert sandbox.call_args.kwargs["runtime"] is resolve.return_value
        kwargs = runner.return_value.launch_prepared.call_args.kwargs
        assert kwargs["runtime"] == launch_arg
        if expected == "krun":
            assert kwargs["annotations"]["krun.cpus"] == "3"
            assert _LAUNCH_ARGS_STUB[0] in kwargs["extra_args"]
            assert "-p" in kwargs["extra_args"]
        else:
            assert "krun.cpus" not in kwargs["annotations"]
            assert kwargs["extra_args"] == []
            _krun_launch_args_stub.assert_not_called()

    @pytest.mark.parametrize("incompatible", ["nested_containers", "services_mode"])
    def test_compatibility_checks_follow_effective_runtime(
        self, _runtime_selection, _experimental_enabled, incompatible
    ) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        project, expected, _ = _runtime_selection
        project = project.model_copy(
            update={incompatible: True if incompatible == "nested_containers" else "socket"}
        )
        if expected == "krun":
            with pytest.raises(SystemExit, match="incompatible"):
                _project_runtime_flags(project, cname="runtime-ctr")
        else:
            flags = _project_runtime_flags(project, cname="runtime-ctr")
            assert ("label=nested" in flags) == (incompatible == "nested_containers")

    def test_shield_refresh_uses_effective_runtime(self, _runtime_selection, tmp_path) -> None:
        from terok.lib.orchestration.task_runners.shield import _refresh_shield_tiers

        project, _, launch_arg = _runtime_selection
        (tmp_path / "shield").mkdir()
        with (
            patch(
                "terok.lib.orchestration.task_runners.shield.get_shield_disable_firewall_no_protection",
                return_value=False,
            ),
            patch("terok.lib.integrations.executor.AgentRoster"),
            patch("terok.lib.core.runtime.resolve_runtime"),
            patch("terok.lib.orchestration.task_runners.container.Sandbox") as sandbox,
        ):
            _refresh_shield_tiers(project, "runtime-ctr", tmp_path)

        assert sandbox.return_value.shield_refresh.call_args.kwargs["runtime"] == launch_arg
