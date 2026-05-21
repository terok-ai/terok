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

from terok.lib.core.runtime import resolve_runtime
from terok.lib.integrations.sandbox import KrunRuntime, NullRuntime, PodmanRuntime
from terok.lib.orchestration.task_runners.container import _validate_krun_compatibility
from tests.testfs import MOCK_BASE


def _krun_project(**overrides):  # type: ignore[no-untyped-def]
    """Build a minimal ProjectConfig with ``runtime="krun"`` (override-friendly)."""
    from terok.lib.core.project_model import ProjectConfig

    defaults: dict[str, object] = {
        "id": "p",
        "security_class": "online",
        "upstream_url": None,
        "default_branch": None,
        "root": MOCK_BASE / "p",
        "tasks_root": MOCK_BASE / "p" / "tasks",
        "gate_path": MOCK_BASE / "p" / "gate",
        "staging_root": None,
        "runtime": "krun",
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)


def _project(**overrides):  # type: ignore[no-untyped-def]
    """Build a minimal ProjectConfig with no runtime set (resolver falls through)."""
    from terok.lib.core.project_model import ProjectConfig

    defaults: dict[str, object] = {
        "id": "demoproj",
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
        with patch("terok.lib.core.config.get_global_run_runtime", return_value=None):
            assert isinstance(resolve_runtime(None), PodmanRuntime)

    def test_env_overrides_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``TEROK_RUNTIME`` env var wins over project + global config."""
        monkeypatch.setenv("TEROK_RUNTIME", "null")
        # Project says krun, global says krun — env still wins with "null".
        with patch("terok.lib.core.runtime._global_runtime_default", return_value="krun"):
            assert isinstance(resolve_runtime(_krun_project()), NullRuntime)

    def test_project_runtime_overrides_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project's ``run.runtime`` beats global ``run.runtime``."""
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
        with patch("terok.lib.core.runtime._global_runtime_default", return_value="krun"):
            # Project explicitly picks crun.
            assert isinstance(resolve_runtime(_project(runtime="crun")), PodmanRuntime)

    def test_global_runtime_used_when_project_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No project value → fall through to global default."""
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
        fake_runtime = MagicMock(spec=KrunRuntime)
        with (
            patch("terok.lib.core.runtime._global_runtime_default", return_value="krun"),
            patch("terok.lib.core.runtime.is_experimental", return_value=True),
            patch("terok.lib.core.runtime.make_krun_runtime", return_value=fake_runtime) as factory,
        ):
            rt = resolve_runtime(_project())  # project.runtime is None
        assert rt is fake_runtime
        factory.assert_called_once()

    def test_krun_requires_experimental_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without ``experimental: true``, krun selection is refused loudly."""
        monkeypatch.setenv("TEROK_RUNTIME", "krun")
        with patch("terok.lib.core.runtime.is_experimental", return_value=False):
            with pytest.raises(SystemExit, match="experimental"):
                resolve_runtime(None)

    def test_krun_with_experimental_delegates_to_executor_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Experimental on + runtime=krun calls executor's `make_krun_runtime`.

        terok no longer owns the host-key materialisation — it just
        flips the runtime selector and lets executor do the rest.
        """
        monkeypatch.setenv("TEROK_RUNTIME", "krun")
        fake_runtime = MagicMock(spec=KrunRuntime)
        with (
            patch("terok.lib.core.runtime.is_experimental", return_value=True),
            patch("terok.lib.core.runtime.make_krun_runtime", return_value=fake_runtime) as factory,
        ):
            rt = resolve_runtime(None)
        assert rt is fake_runtime
        factory.assert_called_once()  # cfg= injected from make_sandbox_config()

    def test_unknown_value_is_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown ``TEROK_RUNTIME`` value raises SystemExit (no silent fallback)."""
        monkeypatch.setenv("TEROK_RUNTIME", "tomato")
        with pytest.raises(SystemExit, match="crun.*krun.*null"):
            resolve_runtime(None)


class TestKrunCompatibilityGuard:
    """`_validate_krun_compatibility` rejects nested-container combos
    and refuses krun selection without the experimental flag."""

    def test_rejects_nested_containers(self) -> None:
        project = _krun_project(nested_containers=True)
        with (
            patch(
                "terok.lib.orchestration.task_runners.container.is_experimental",
                return_value=True,
            ),
            pytest.raises(SystemExit, match="incompatible.*nested_containers"),
        ):
            _validate_krun_compatibility(project)

    def test_accepts_krun_without_nested(self) -> None:
        project = _krun_project()
        with patch(
            "terok.lib.orchestration.task_runners.container.is_experimental",
            return_value=True,
        ):
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


@pytest.fixture()
def _experimental_enabled():
    """Patch ``is_experimental`` true for tests that exercise the krun path.

    The compatibility guard refuses krun selection unless the
    experimental flag is set; these tests verify the happy-path
    *after* the gate, so the gate is patched out.
    """
    with patch(
        "terok.lib.orchestration.task_runners.container.is_experimental",
        return_value=True,
    ):
        yield


_LAUNCH_ARGS_STUB = ["__stub_launch_args__"]


@pytest.fixture()
def _krun_launch_args_stub():
    """Patch `krun_launch_args` to return a sentinel list.

    Terok's job is to splice executor's helper output into its flag
    stream — the helper's contents (host-pubkey mount, sshd-trigger
    env var, ``--user root``) are pinned in
    [`tests.unit.test_krun.TestKrunLaunchArgs`][terok_executor.tests]
    on the executor side.
    """
    with patch(
        "terok.lib.orchestration.task_runners.container.krun_launch_args",
        return_value=list(_LAUNCH_ARGS_STUB),
    ) as m:
        yield m


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

    def test_krun_emits_runtime_flag(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        assert "--runtime" in flags
        assert flags[flags.index("--runtime") + 1] == "krun"

    def test_krun_emits_loopback_pinned_port_forward(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        """``-p 127.0.0.1:<reserved>:22`` — the host side of the forward is
        pinned to loopback so pasta doesn't expose the krun task's sshd on
        external interfaces.  No terok-private annotation: podman already
        tracks the mapping; ``podman_port_resolver`` reads it back via
        ``podman port`` at exec time."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")

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

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
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

    def test_krun_emits_no_oci_krun_annotations(
        self, _experimental_enabled, _krun_launch_args_stub, _stub_port_reservation
    ) -> None:
        """Regression guard: terok must not emit ``run.oci.krun.*``
        annotations — sizing goes through the standard ``run.memory`` /
        ``run.cpus`` → podman ``--memory`` / ``--cpus`` path."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", memory="4g", cpus="2"),
            cname="terok-cli-demoproj-task-a",
        )
        joined = " ".join(flags)
        assert "run.oci.krun" not in joined
        assert "krun.cpus" not in joined
        assert "krun.ram_mib" not in joined

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
            _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")

    def test_nested_only_unaffected(self, _krun_launch_args_stub) -> None:
        """`nested_containers` alone still emits its existing flags (no krun bits)."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(nested_containers=True), cname="terok-cli-demoproj-task-a"
        )
        assert "label=nested" in flags
        assert "--runtime" not in flags
        _krun_launch_args_stub.assert_not_called()


class TestChainKrunDnsRewrite:
    """`_chain_krun_dns_rewrite` retargets shield's bind-mounted resolv.conf
    from ``127.0.0.1`` (dnsmasq, unreachable from inside the krun guest)
    to ``169.254.1.1`` (pasta forwarder, both reachable via TSI and
    permitted by shield's nft policy)."""

    def test_rewrites_existing_shield_resolv_conf(self, tmp_path) -> None:
        from terok.lib.orchestration.task_runners.container import _chain_krun_dns_rewrite

        shield_dir = tmp_path / "shield"
        shield_dir.mkdir()
        resolv = shield_dir / "resolv.conf"
        resolv.write_text("nameserver 127.0.0.1\noptions ndots:0\n")

        chained = _chain_krun_dns_rewrite(None, tmp_path)
        chained.pre_start()

        assert resolv.read_text() == "nameserver 169.254.1.1\noptions ndots:0\n"

    def test_no_op_when_shield_did_not_write_file(self, tmp_path) -> None:
        """Shield bypass / non-dnsmasq tier → no file → no error, no write."""
        from terok.lib.orchestration.task_runners.container import _chain_krun_dns_rewrite

        chained = _chain_krun_dns_rewrite(None, tmp_path)
        chained.pre_start()  # no raise
        assert not (tmp_path / "shield" / "resolv.conf").exists()

    def test_runs_caller_pre_start_first(self, tmp_path) -> None:
        """An existing ``pre_start`` callback is fired before the rewrite,
        not silently dropped."""
        from terok.lib.integrations.sandbox import LifecycleHooks
        from terok.lib.orchestration.task_runners.container import _chain_krun_dns_rewrite

        events: list[str] = []
        shield_dir = tmp_path / "shield"
        shield_dir.mkdir()
        (shield_dir / "resolv.conf").write_text("nameserver 127.0.0.1\n")

        def _prior() -> None:
            events.append("prior")

        chained = _chain_krun_dns_rewrite(LifecycleHooks(pre_start=_prior), tmp_path)
        chained.pre_start()

        assert events == ["prior"]
        assert "169.254.1.1" in (shield_dir / "resolv.conf").read_text()

    def test_preserves_other_hook_slots(self, tmp_path) -> None:
        """``post_start`` / ``post_ready`` / ``post_stop`` pass through
        untouched — only ``pre_start`` is the chain point."""
        from terok.lib.integrations.sandbox import LifecycleHooks
        from terok.lib.orchestration.task_runners.container import _chain_krun_dns_rewrite

        post_start = lambda: None  # noqa: E731 — identity matters, not contents
        post_ready = lambda: None  # noqa: E731
        post_stop = lambda: None  # noqa: E731
        chained = _chain_krun_dns_rewrite(
            LifecycleHooks(post_start=post_start, post_ready=post_ready, post_stop=post_stop),
            tmp_path,
        )
        assert chained.post_start is post_start
        assert chained.post_ready is post_ready
        assert chained.post_stop is post_stop
