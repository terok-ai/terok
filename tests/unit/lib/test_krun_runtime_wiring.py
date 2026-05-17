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
from terok.lib.orchestration.task_runners.container import (
    _allocate_krun_cid,
    _validate_krun_compatibility,
)
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


class TestKrunCidAllocation:
    """The CID allocator is stable, deterministic, per-container, and unreserved."""

    def test_stable_for_same_container(self) -> None:
        assert _allocate_krun_cid("task-proj-x") == _allocate_krun_cid("task-proj-x")

    def test_differs_across_containers(self) -> None:
        assert _allocate_krun_cid("task-a") != _allocate_krun_cid("task-b")

    def test_per_container_under_same_project(self) -> None:
        """Two concurrent tasks under the same project get distinct CIDs.

        Keying on the container name (which already encodes project +
        mode + task) means same-project multi-task — the most likely
        collision shape — doesn't collide.  Without this, every task
        after the first under one project would fail to start because
        the vsock kernel module rejects a duplicate CID bind.
        """
        a = _allocate_krun_cid("terok-cli-projx-taskA")
        b = _allocate_krun_cid("terok-cli-projx-taskB")
        assert a != b

    def test_avoids_reserved_low_cids(self) -> None:
        """CIDs 0, 1, 2 are reserved by the vsock spec."""
        for cname in ("a", "abc", "x" * 50, "terok-cli-proj-task-with-dashes"):
            assert _allocate_krun_cid(cname) >= 3


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


class TestProjectRuntimeFlags:
    """`_project_runtime_flags` emits --runtime + krun annotations + pubkey mount."""

    def test_no_runtime_no_flags(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        assert _project_runtime_flags(_project(), cname="terok-cli-demoproj-task-a") == []

    def test_krun_emits_runtime_flag(self, _experimental_enabled, _krun_keypair) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        assert "--runtime" in flags
        assert flags[flags.index("--runtime") + 1] == "krun"

    def test_krun_emits_cid_annotation(self, _experimental_enabled, _krun_keypair) -> None:
        from terok.lib.integrations.sandbox import DEFAULT_CID_ANNOTATION
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(_project(runtime="krun"), cname="terok-cli-demoproj-task-a")
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        cid_annotations = [a for a in annotations if a.startswith(DEFAULT_CID_ANNOTATION + "=")]
        assert len(cid_annotations) == 1
        # The value is an integer in the unreserved CID range.
        _, _, cid_str = cid_annotations[0].partition("=")
        assert int(cid_str) >= 3

    def test_krun_cid_differs_per_container(self, _experimental_enabled, _krun_keypair) -> None:
        """Two containers under the same project get distinct CID annotations.

        Pins the per-container keying — same-project multi-task is the
        most likely collision shape, and the kernel rejects duplicate
        vsock CID binds, so without this property the second task
        under a project would fail to start.
        """
        from terok.lib.integrations.sandbox import DEFAULT_CID_ANNOTATION
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        def cid_of(cname: str) -> str:
            flags = _project_runtime_flags(_project(runtime="krun"), cname=cname)
            (entry,) = [
                flags[i + 1]
                for i, t in enumerate(flags)
                if t == "--annotation" and flags[i + 1].startswith(DEFAULT_CID_ANNOTATION + "=")
            ]
            return entry

        assert cid_of("terok-cli-demoproj-task-a") != cid_of("terok-cli-demoproj-task-b")

    def test_krun_emits_pubkey_bind_mount(self, _experimental_enabled, _krun_keypair) -> None:
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

    def test_krun_emits_cpu_and_ram_when_set(self, _experimental_enabled, _krun_keypair) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            _project(runtime="krun", krun_cpus=4, krun_ram_mib=8192),
            cname="terok-cli-demoproj-task-a",
        )
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        assert "run.oci.krun.cpus=4" in annotations
        assert "run.oci.krun.ram_mib=8192" in annotations

    def test_krun_skips_cpu_ram_when_unset(self, _experimental_enabled, _krun_keypair) -> None:
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
