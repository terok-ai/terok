# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok's krun wiring: get_runtime() krun branch, %host keypair
helper, project config plumbing, and the `run.runtime: krun` flag set
emitted at task launch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.core import runtime as runtime_mod
from terok.lib.core.runtime import ensure_krun_host_keypair, get_runtime
from terok.lib.integrations.sandbox import KrunRuntime, NullRuntime, PodmanRuntime
from terok.lib.orchestration.task_runners.container import (
    _allocate_krun_cid,
    _validate_krun_compatibility,
)


@pytest.fixture(autouse=True)
def _reset_runtime():
    """Each test starts with a fresh runtime cache."""
    runtime_mod.reset_runtime()
    yield
    runtime_mod.reset_runtime()


class TestGetRuntimeKrunBranch:
    """`get_runtime()` selects `KrunRuntime` only with experimental + the env."""

    def test_default_is_podman(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEROK_RUNTIME", raising=False)
        assert isinstance(get_runtime(), PodmanRuntime)

    def test_null_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEROK_RUNTIME", "null")
        assert isinstance(get_runtime(), NullRuntime)

    def test_krun_requires_experimental_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without `experimental: true`, krun selection is refused loudly."""
        monkeypatch.setenv("TEROK_RUNTIME", "krun")
        with patch("terok.lib.core.runtime.is_experimental", return_value=False):
            with pytest.raises(SystemExit, match="experimental"):
                get_runtime()

    def test_krun_with_experimental_returns_krun_runtime(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Experimental on + env=krun yields a KrunRuntime backed by VsockSSHTransport."""
        monkeypatch.setenv("TEROK_RUNTIME", "krun")
        with (
            patch("terok.lib.core.runtime.is_experimental", return_value=True),
            patch("terok.lib.core.runtime.ensure_krun_host_keypair", return_value=tmp_path / "k"),
        ):
            rt = get_runtime()
        assert isinstance(rt, KrunRuntime)

    def test_unknown_value_is_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEROK_RUNTIME", "tomato")
        with pytest.raises(SystemExit, match="podman.*null.*krun"):
            get_runtime()


class TestEnsureKrunHostKeypair:
    """`ensure_krun_host_keypair` generates once, returns the cached path after."""

    def test_creates_keypair_when_missing(self, tmp_path: Path) -> None:
        """First call shells out to ssh-keygen; both halves end up on disk."""
        private_path = tmp_path / "krun_host.key"

        def fake_keygen(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
            # Mirror what real ssh-keygen would write so the existence
            # check passes on second invocation.
            private_path.write_text("fake-key\n")
            (tmp_path / "krun_host.key.pub").write_text("ssh-ed25519 AAAA test\n")
            return None

        with patch("subprocess.run", side_effect=fake_keygen) as run:
            result = ensure_krun_host_keypair(runtime_dir=tmp_path)
        assert result == private_path
        argv = run.call_args[0][0]
        assert argv[0] == "ssh-keygen"
        assert "-t" in argv and argv[argv.index("-t") + 1] == "ed25519"

    def test_idempotent_when_keypair_exists(self, tmp_path: Path) -> None:
        """Second call returns the path without re-invoking ssh-keygen."""
        (tmp_path / "krun_host.key").write_text("existing\n")
        (tmp_path / "krun_host.key.pub").write_text("ssh-ed25519 AAAA existing\n")
        with patch("subprocess.run") as run:
            ensure_krun_host_keypair(runtime_dir=tmp_path)
        run.assert_not_called()

    def test_self_heals_orphan_private_key(self, tmp_path: Path) -> None:
        """Private-only state derives the missing pubkey via ssh-keygen -y.

        Rotating the keypair here would brick guest images baked against
        the prior pubkey, so we must derive the public from the existing
        private rather than minting fresh material.
        """
        private = tmp_path / "krun_host.key"
        public = tmp_path / "krun_host.key.pub"
        private.write_text("existing-private-bytes\n")

        from subprocess import CompletedProcess

        with patch(
            "subprocess.run",
            return_value=CompletedProcess(
                args=[],
                returncode=0,
                stdout="ssh-ed25519 AAAArecovered krun-host\n",
                stderr="",
            ),
        ) as run:
            ensure_krun_host_keypair(runtime_dir=tmp_path)

        argv = run.call_args[0][0]
        assert argv[:3] == ["ssh-keygen", "-y", "-f"]
        assert public.exists()
        assert public.read_text().startswith("ssh-ed25519 AAAArecovered")

    def test_orphan_public_is_cleared_and_keypair_regenerated(self, tmp_path: Path) -> None:
        """Public-only state means the private was lost — start fresh.

        Without unlinking the orphan, ssh-keygen would prompt "overwrite?"
        and hang under the non-interactive subprocess invocation.
        """
        private = tmp_path / "krun_host.key"
        public = tmp_path / "krun_host.key.pub"
        public.write_text("ssh-ed25519 AAAAorphan krun-host\n")

        def fake_keygen(argv, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            private.write_text("new-private\n")
            public.write_text("ssh-ed25519 AAAAnew krun-host\n")
            return None

        with patch("subprocess.run", side_effect=fake_keygen) as run:
            ensure_krun_host_keypair(runtime_dir=tmp_path)

        argv = run.call_args[0][0]
        assert argv[:4] == ["ssh-keygen", "-t", "ed25519", "-f"]
        assert "AAAAnew" in public.read_text()


class TestKrunCidAllocation:
    """The placeholder CID allocator is stable, deterministic, and unreserved."""

    def test_stable_for_same_project(self) -> None:
        assert _allocate_krun_cid("proj-x") == _allocate_krun_cid("proj-x")

    def test_differs_across_projects(self) -> None:
        assert _allocate_krun_cid("a") != _allocate_krun_cid("b")

    def test_avoids_reserved_low_cids(self) -> None:
        """CIDs 0, 1, 2 are reserved by the vsock spec."""
        for project in ("a", "abc", "x" * 50, "proj-with-dashes"):
            assert _allocate_krun_cid(project) >= 3


class TestKrunCompatibilityGuard:
    """`_validate_krun_compatibility` rejects nested-container combos."""

    def test_rejects_nested_containers(self) -> None:
        from terok.lib.core.project_model import ProjectConfig

        project = ProjectConfig(
            id="p",
            security_class="online",
            upstream_url=None,
            default_branch=None,
            root=Path("/tmp/p"),  # noqa: S108
            tasks_root=Path("/tmp/p/tasks"),  # noqa: S108
            gate_path=Path("/tmp/p/gate"),  # noqa: S108
            staging_root=None,
            nested_containers=True,
            runtime="krun",
        )
        with pytest.raises(SystemExit, match="incompatible.*nested_containers"):
            _validate_krun_compatibility(project)

    def test_accepts_krun_without_nested(self) -> None:
        from terok.lib.core.project_model import ProjectConfig

        project = ProjectConfig(
            id="p",
            security_class="online",
            upstream_url=None,
            default_branch=None,
            root=Path("/tmp/p"),  # noqa: S108
            tasks_root=Path("/tmp/p/tasks"),  # noqa: S108
            gate_path=Path("/tmp/p/gate"),  # noqa: S108
            staging_root=None,
            runtime="krun",
        )
        _validate_krun_compatibility(project)  # no raise


class TestProjectRuntimeFlags:
    """`_project_runtime_flags` emits --runtime + krun annotations."""

    def _project(self, **overrides) -> object:  # type: ignore[no-untyped-def]
        from terok.lib.core.project_model import ProjectConfig

        defaults: dict[str, object] = {
            "id": "demoproj",
            "security_class": "online",
            "upstream_url": None,
            "default_branch": None,
            "root": Path("/tmp/demoproj"),  # noqa: S108
            "tasks_root": Path("/tmp/demoproj/tasks"),  # noqa: S108
            "gate_path": Path("/tmp/demoproj/gate"),  # noqa: S108
            "staging_root": None,
        }
        defaults.update(overrides)
        return ProjectConfig(**defaults)

    def test_no_runtime_no_flags(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        assert _project_runtime_flags(self._project()) == []

    def test_krun_emits_runtime_flag(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(runtime="krun"))
        assert "--runtime" in flags
        assert flags[flags.index("--runtime") + 1] == "krun"

    def test_krun_emits_cid_annotation(self) -> None:
        from terok.lib.integrations.sandbox import DEFAULT_CID_ANNOTATION
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(runtime="krun"))
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        cid_annotations = [a for a in annotations if a.startswith(DEFAULT_CID_ANNOTATION + "=")]
        assert len(cid_annotations) == 1
        # The value is an integer in the unreserved CID range.
        _, _, cid_str = cid_annotations[0].partition("=")
        assert int(cid_str) >= 3

    def test_krun_emits_cpu_and_ram_when_set(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            self._project(runtime="krun", krun_cpus=4, krun_ram_mib=8192)
        )
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        assert "run.oci.krun.cpus=4" in annotations
        assert "run.oci.krun.ram_mib=8192" in annotations

    def test_krun_skips_cpu_ram_when_unset(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(runtime="krun"))
        joined = " ".join(flags)
        assert "krun.cpus" not in joined
        assert "krun.ram_mib" not in joined

    def test_krun_plus_nested_rejected(self) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        with pytest.raises(SystemExit, match="incompatible.*nested"):
            _project_runtime_flags(self._project(runtime="krun", nested_containers=True))

    def test_nested_only_unaffected(self) -> None:
        """`nested_containers` alone still emits its existing flags."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(nested_containers=True))
        assert "label=nested" in flags
        assert "--runtime" not in flags
