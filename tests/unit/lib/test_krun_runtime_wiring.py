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


@pytest.fixture()
def _vault_backed(tmp_path: Path):
    """Wire ``ensure_krun_host_keypair`` to an in-memory vault DB.

    Patches ``make_sandbox_config`` so the helper opens a fresh
    ``CredentialDB`` against a per-test temp path with a known
    passphrase — no real vault unlock needed.
    """
    from unittest.mock import MagicMock

    from terok_sandbox import CredentialDB

    db_path = tmp_path / "vault" / "credentials.db"

    def _open(*, prompt_on_tty: bool = False) -> CredentialDB:
        # New connection per call — mirrors the production flow where
        # each call opens and closes its own handle.
        return CredentialDB(db_path, passphrase="test")

    cfg = MagicMock()
    cfg.open_credential_db = _open
    with patch("terok.lib.core.runtime.make_sandbox_config", return_value=cfg):
        yield


class TestEnsureKrunHostKeypair:
    """`ensure_krun_host_keypair` mints via the vault, materialises to tmpfs.

    The vault is the system of record (``%host`` infrastructure scope);
    these tests use a per-test ``CredentialDB`` patched in via
    ``make_sandbox_config`` to keep the production wiring honest while
    exercising real key generation + storage.
    """

    def test_creates_keypair_when_missing(self, tmp_path: Path, _vault_backed) -> None:
        """First call mints in the vault, writes 0600 OpenSSH PEM to tmpfs."""
        runtime_dir = tmp_path / "runtime"
        result = ensure_krun_host_keypair(runtime_dir=runtime_dir)

        assert result == runtime_dir / "krun_host.key"
        private = result.read_bytes()
        assert private.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
        # 0o600 = owner-only read/write; matches what ``ssh -i`` requires.
        assert (result.stat().st_mode & 0o777) == 0o600

        public = runtime_dir / "krun_host.key.pub"
        assert public.exists()
        line = public.read_text()
        assert line.startswith("ssh-ed25519 ")
        assert line.rstrip().endswith("krun-host (terok)")

    def test_refuses_persistent_disk_when_no_xdg_runtime_dir(
        self, _vault_backed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``$XDG_RUNTIME_DIR`` → refuse to write private bytes to disk.

        The default ``namespace_runtime_dir()`` would otherwise fall
        back to ``$XDG_STATE_HOME/terok`` (persistent disk).  Letting
        the vault-backed private key land there defeats the whole
        "vault is the system of record, tmpfs is a transient handle"
        property — fail closed instead.
        """
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        with pytest.raises(SystemExit, match="requires .*XDG_RUNTIME_DIR"):
            ensure_krun_host_keypair()  # no explicit runtime_dir

    def test_tightens_existing_dir_to_0700(self, tmp_path: Path, _vault_backed) -> None:
        """A pre-existing runtime dir wider than 0700 is re-tightened.

        ``mkdir(mode=0o700, exist_ok=True)`` is no-op for an existing
        dir, so a previous run under a more permissive umask could
        leave the cache dir world-listable.  Re-chmod every time.
        """
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir(mode=0o755)  # too wide
        ensure_krun_host_keypair(runtime_dir=runtime_dir)
        assert (runtime_dir.stat().st_mode & 0o777) == 0o700

    def test_private_write_is_atomic_no_symlink_clobber(
        self, tmp_path: Path, _vault_backed
    ) -> None:
        """A symlink at the target path is replaced atomically, not followed.

        ``os.replace`` is atomic and never follows a symlink at the
        destination — so an attacker who pre-creates ``krun_host.key``
        as a symlink to ``/etc/passwd`` can't trick us into writing
        the PEM through to that target.  The replace cuts the symlink
        out of the way, leaving a regular file with the PEM bytes.
        """
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir(mode=0o700)
        # Pre-plant a hostile symlink at the destination.
        decoy = tmp_path / "decoy-target"
        decoy.write_text("untouched")
        (runtime_dir / "krun_host.key").symlink_to(decoy)

        ensure_krun_host_keypair(runtime_dir=runtime_dir)

        # The destination is now a regular file with PEM bytes — not
        # a symlink that wrote through to the decoy.
        priv = runtime_dir / "krun_host.key"
        assert not priv.is_symlink()
        assert priv.read_bytes().startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
        # The decoy target is unchanged — we did not follow the symlink.
        assert decoy.read_text() == "untouched"

    def test_public_write_also_resists_symlink_clobber(self, tmp_path: Path, _vault_backed) -> None:
        """Same atomic-replace protection applies to the public key file."""
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir(mode=0o700)
        decoy = tmp_path / "decoy-pub"
        decoy.write_text("untouched")
        (runtime_dir / "krun_host.key.pub").symlink_to(decoy)

        ensure_krun_host_keypair(runtime_dir=runtime_dir)

        pub = runtime_dir / "krun_host.key.pub"
        assert not pub.is_symlink()
        assert pub.read_text().startswith("ssh-ed25519 ")
        assert decoy.read_text() == "untouched"

    def test_idempotent_returns_same_key_material(self, tmp_path: Path, _vault_backed) -> None:
        """Second call reloads the existing %host key — same public line.

        The on-disk private bytes differ across calls because OpenSSH
        PEM serialisation embeds a random ``checkint`` — compare the
        public line (stable identity) instead.
        """
        runtime_dir = tmp_path / "runtime"
        ensure_krun_host_keypair(runtime_dir=runtime_dir)
        first_pub = (runtime_dir / "krun_host.key.pub").read_text()

        ensure_krun_host_keypair(runtime_dir=runtime_dir)
        second_pub = (runtime_dir / "krun_host.key.pub").read_text()

        assert second_pub == first_pub

    def test_tmpfs_cache_rewritten_from_vault_on_every_call(
        self, tmp_path: Path, _vault_backed
    ) -> None:
        """Out-of-band tmpfs tampering is overwritten from the vault.

        The vault is the source of truth — if an operator (or anything
        else) modifies the tmpfs private file between calls, the next
        call must restore it.  This is what makes vault-side rotation
        propagate without manual intervention.
        """
        runtime_dir = tmp_path / "runtime"
        ensure_krun_host_keypair(runtime_dir=runtime_dir)
        priv = runtime_dir / "krun_host.key"
        priv.write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nGARBAGE\n")

        ensure_krun_host_keypair(runtime_dir=runtime_dir)

        # The garbage was replaced with a real PEM from the vault.
        restored = priv.read_bytes()
        assert restored.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
        assert b"GARBAGE" not in restored
        # Still 0600 after the rewrite (even if the operator's chmod
        # widened it on inspection).
        assert (priv.stat().st_mode & 0o777) == 0o600

    def test_pubkey_is_baked_in_authorized_keys_form(self, tmp_path: Path, _vault_backed) -> None:
        """The .pub file is exactly what L0G ``build_l0g_image`` consumes.

        Loose round-trip: parse the public line via cryptography to
        confirm it's a valid OpenSSH public key — that's the contract
        ``ssh`` and ``authorized_keys`` rely on.
        """
        from cryptography.hazmat.primitives import serialization

        runtime_dir = tmp_path / "runtime"
        ensure_krun_host_keypair(runtime_dir=runtime_dir)
        line = (runtime_dir / "krun_host.key.pub").read_text().strip()
        # Strip the comment trailer for parsing (``cryptography`` accepts
        # the bare ``<type> <b64>``).
        key_part = " ".join(line.split()[:2])
        serialization.load_ssh_public_key(key_part.encode())  # no raise


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
    """`_validate_krun_compatibility` rejects nested-container combos
    and refuses krun selection without the experimental flag."""

    def _krun_project(self, **overrides):  # type: ignore[no-untyped-def]
        from terok.lib.core.project_model import ProjectConfig

        defaults: dict[str, object] = {
            "id": "p",
            "security_class": "online",
            "upstream_url": None,
            "default_branch": None,
            "root": Path("/tmp/p"),  # noqa: S108
            "tasks_root": Path("/tmp/p/tasks"),  # noqa: S108
            "gate_path": Path("/tmp/p/gate"),  # noqa: S108
            "staging_root": None,
            "runtime": "krun",
        }
        defaults.update(overrides)
        return ProjectConfig(**defaults)

    def test_rejects_nested_containers(self) -> None:
        project = self._krun_project(nested_containers=True)
        with (
            patch(
                "terok.lib.orchestration.task_runners.container.is_experimental",
                return_value=True,
            ),
            pytest.raises(SystemExit, match="incompatible.*nested_containers"),
        ):
            _validate_krun_compatibility(project)

    def test_accepts_krun_without_nested(self) -> None:
        project = self._krun_project()
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
        project = self._krun_project()
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

    The compatibility guard now refuses krun selection unless the
    experimental flag is set — that's the gate we want to honour in
    every path, including config-driven.  These tests verify the
    happy-path *after* the gate, so the gate is patched out.
    """
    with patch(
        "terok.lib.orchestration.task_runners.container.is_experimental",
        return_value=True,
    ):
        yield


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

    def test_krun_emits_runtime_flag(self, _experimental_enabled) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(runtime="krun"))
        assert "--runtime" in flags
        assert flags[flags.index("--runtime") + 1] == "krun"

    def test_krun_emits_cid_annotation(self, _experimental_enabled) -> None:
        from terok.lib.integrations.sandbox import DEFAULT_CID_ANNOTATION
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(runtime="krun"))
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        cid_annotations = [a for a in annotations if a.startswith(DEFAULT_CID_ANNOTATION + "=")]
        assert len(cid_annotations) == 1
        # The value is an integer in the unreserved CID range.
        _, _, cid_str = cid_annotations[0].partition("=")
        assert int(cid_str) >= 3

    def test_krun_emits_cpu_and_ram_when_set(self, _experimental_enabled) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(
            self._project(runtime="krun", krun_cpus=4, krun_ram_mib=8192)
        )
        annotations = [flags[i + 1] for i, t in enumerate(flags) if t == "--annotation"]
        assert "run.oci.krun.cpus=4" in annotations
        assert "run.oci.krun.ram_mib=8192" in annotations

    def test_krun_skips_cpu_ram_when_unset(self, _experimental_enabled) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(runtime="krun"))
        joined = " ".join(flags)
        assert "krun.cpus" not in joined
        assert "krun.ram_mib" not in joined

    def test_krun_plus_nested_rejected(self, _experimental_enabled) -> None:
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        with pytest.raises(SystemExit, match="incompatible.*nested"):
            _project_runtime_flags(self._project(runtime="krun", nested_containers=True))

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
            _project_runtime_flags(self._project(runtime="krun"))

    def test_nested_only_unaffected(self) -> None:
        """`nested_containers` alone still emits its existing flags."""
        from terok.lib.orchestration.task_runners.container import _project_runtime_flags

        flags = _project_runtime_flags(self._project(nested_containers=True))
        assert "label=nested" in flags
        assert "--runtime" not in flags
