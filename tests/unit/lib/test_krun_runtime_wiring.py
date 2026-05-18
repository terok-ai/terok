# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok's krun wiring: `resolve_runtime` priority chain,
project config plumbing, the `run.runtime: krun` flag set emitted at
task launch, and the bind-mount that delivers the host pubkey into
the guest.  Host-side keypair provisioning + the runtime factory
itself live in terok-executor (`terok_executor.krun`) and are tested
there.
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


@pytest.fixture()
def _krun_keypair(tmp_path):
    """Patch `ensure_krun_host_keypair` to return a deterministic stub keypair
    so flag-emission tests don't have to materialise a real vault key."""
    from terok_executor.krun import KrunHostKeypair

    fake = KrunHostKeypair(
        private_path=tmp_path / "krun_host.key",
        public_path=tmp_path / "krun_host.key.pub",
        public_line="ssh-ed25519 AAAA test krun-host (terok)",
        fingerprint="SHA256:fake",
        created=False,
    )
    with patch(
        "terok.lib.orchestration.task_runners.container.ensure_krun_host_keypair",
        return_value=fake,
    ):
        yield fake


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
    """`_project_runtime_flags` emits --runtime + krun annotations + port-forward + pubkey mount."""

    def test_no_runtime_no_flags(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        assert _project_runtime_flags(_project(), cname="terok-cli-demoproj-task-a") == []

    def test_krun_emits_runtime_flag(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
    ) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        assert "--runtime" in flags
        assert flags[flags.index("--runtime") + 1] == "krun"

    def test_krun_emits_loopback_pinned_port_forward(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
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

    def test_krun_emits_pubkey_bind_mount(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
    ) -> None:
        """The live host pubkey is bind-mounted into the guest at task launch.

        This is what keeps the L0 image byte-identical across crun/krun:
        no per-installation secret baked at build time; the orchestrator
        threads ``ensure_krun_host_keypair().public_path`` in at launch.
        """
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        # The volume mount is added as ``-v <host>:<guest>:ro``.
        v_idx = flags.index("-v")
        mount_spec = flags[v_idx + 1]
        assert "/etc/ssh/authorized_keys.d/terok:ro" in mount_spec
        assert str(_krun_keypair.public_path) in mount_spec

    def test_krun_emits_container_runtime_env_var(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
    ) -> None:
        """``-e TEROK_CONTAINER_RUNTIME=krun`` is the explicit runtime signal
        the in-container init script gates the sshd supervisor on.  Explicit
        env var rather than inferring from the bind-mount, so a botched L0
        with a non-empty authorized_keys placeholder can't accidentally
        expose sshd under crun."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        env_assignments = [flags[i + 1] for i, t in enumerate(flags) if t == "-e"]
        assert "TEROK_CONTAINER_RUNTIME=krun" in env_assignments

    def test_non_krun_does_not_emit_container_runtime_env_var(self) -> None:
        """Under any runtime other than krun, the sshd-trigger env var must
        be absent — otherwise an L0 with an accidentally-populated
        authorized_keys file could expose sshd in a crun task."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(), cname="terok-cli-demoproj-task-a")
        env_assignments = [flags[i + 1] for i, t in enumerate(flags) if t == "-e"]
        assert not any(e.startswith("TEROK_CONTAINER_RUNTIME") for e in env_assignments)

    def test_krun_launches_as_root_overriding_image_user(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
    ) -> None:
        """``--user root`` overrides the L0's ``USER dev`` directive only
        under krun — the in-guest sshd needs to start as root so it can
        listen on TCP 22, write to /run/sshd, and drop to the authenticated
        user on connection.  Crun keeps the image's ``USER dev`` (correct
        for AI agents that refuse uid 0)."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        user_idx = flags.index("--user")
        assert flags[user_idx + 1] == "root"

    def test_non_krun_does_not_override_image_user(self) -> None:
        """Under crun the image's ``USER dev`` is what we want — terok must
        not pass ``--user`` and silently elevate the agent to root."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(), cname="terok-cli-demoproj-task-a")
        assert "--user" not in flags

    def test_krun_emits_cpu_and_ram_when_set(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
    ) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", krun_cpus=4, krun_ram_mib=8192),
            cname="terok-cli-demoproj-task-a",
        )
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        assert "run.oci.krun.cpus=4" in annotations
        assert "run.oci.krun.ram_mib=8192" in annotations

    def test_krun_skips_cpu_ram_when_unset(
        self, _experimental_enabled, _krun_keypair, _stub_port_reservation
    ) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        joined = " ".join(flags)
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

    def test_nested_only_unaffected(self) -> None:
        """`nested_containers` alone still emits its existing flags (no krun bits)."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(nested_containers=True), cname="terok-cli-demoproj-task-a"
        )
        assert "label=nested" in flags
        assert "--runtime" not in flags
        # No krun-mode mount when runtime != krun.
        assert "/etc/ssh/authorized_keys.d/terok:ro" not in " ".join(flags)
