# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Process-wide `ContainerRuntime` accessor.

Centralises backend construction so the sandbox-boundary
import-linter ratchet stays tight: every call site asks this module
for its runtime handle instead of instantiating ``PodmanRuntime``
locally.

Selection lives in two places, in priority order:

- ``TEROK_RUNTIME`` env var (``podman`` | ``null`` | ``krun``) — the
  override path used by ``terok task start`` invocations that want to
  switch runtime without editing project.yml.
- ``run.runtime`` in project.yml — the config-first path queried by
  task launch code when the env var is unset.

``krun`` requires the global ``experimental: true`` flag.  Without it,
selection raises ``SystemExit`` at startup with a clear pointer to the
opt-in — a misspelled env var or accidental config edit should never
silently boot the experimental backend.

Tests that need a specific backend should call [`set_runtime`][terok.lib.core.runtime.set_runtime]
in setup and [`reset_runtime`][terok.lib.core.runtime.reset_runtime] in teardown.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 — ssh-keygen wrapper for %host bootstrap
from pathlib import Path

from terok.lib.core.config import is_experimental
from terok.lib.integrations.sandbox import (
    ContainerRuntime,
    KrunRuntime,
    NullRuntime,
    PodmanRuntime,
    VsockSSHTransport,
    namespace_runtime_dir,
    podman_annotation_resolver,
)

_runtime: ContainerRuntime | None = None

# Names matching the L0G guest image's baked-in trust file.  Keep both
# halves co-located so a future rename touches one place.
_HOST_KEYPAIR_BASENAME = "krun_host"


def get_runtime() -> ContainerRuntime:
    """Return the cached process-wide `ContainerRuntime`.

    On first call, inspects ``TEROK_RUNTIME`` and constructs the matching
    backend.  Supported values:

    - ``"podman"`` (default) — conventional container runtime.
    - ``"null"`` — in-memory stub for CI / dry-run.
    - ``"krun"`` — KVM microVM isolation; **requires**
      ``experimental: true`` in the global config.

    Any other value raises [`SystemExit`][SystemExit] at startup rather
    than quietly falling back.
    """
    global _runtime
    if _runtime is None:
        backend = os.environ.get("TEROK_RUNTIME", "podman").strip().lower()
        if backend == "podman":
            _runtime = PodmanRuntime()
        elif backend == "null":
            _runtime = NullRuntime()
        elif backend == "krun":
            _runtime = _build_krun_runtime()
        else:
            raise SystemExit(f"TEROK_RUNTIME={backend!r}: expected 'podman', 'null', or 'krun'")
    return _runtime


def _build_krun_runtime() -> KrunRuntime:
    """Construct a `KrunRuntime` with the production vsock-SSH transport.

    Gates on the global ``experimental`` flag — the krun backend is
    Phase 3, opt-in only.  Provisions a host-side ``%host`` keypair to
    the tmpfs runtime dir if one doesn't exist; the matching public key
    is baked into the L0G guest image at build time.
    """
    if not is_experimental():
        raise SystemExit(
            "TEROK_RUNTIME=krun requires the experimental flag.  Set "
            "`experimental: true` in your config.yml, or pass "
            "`--experimental` on the command line."
        )

    identity_file = ensure_krun_host_keypair()
    transport = VsockSSHTransport(
        identity_file=identity_file,
        endpoint_resolver=podman_annotation_resolver(),
    )
    return KrunRuntime(transport=transport, podman=PodmanRuntime())


def ensure_krun_host_keypair(
    runtime_dir: Path | None = None,
) -> Path:
    """Provision the host-side ed25519 keypair used by the krun transport.

    Generates the keypair under *runtime_dir* (default:
    [`namespace_runtime_dir()`][terok.lib.integrations.sandbox.namespace_runtime_dir])
    on first call; subsequent calls return the cached path.  The public
    half (``krun_host.pub``) must be baked into the L0G guest image at
    build time so the guest accepts our auth.

    The full vault-backed ``%host`` scope flow is a follow-up — see
    Phase 3 step 1 in terok-ai/terok#767, which already loosened the
    scope-name validator to accept ``%host`` for that future move.
    """
    target_dir = runtime_dir or namespace_runtime_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    private = target_dir / f"{_HOST_KEYPAIR_BASENAME}.key"
    public = target_dir / f"{_HOST_KEYPAIR_BASENAME}.key.pub"
    if private.exists() and public.exists():
        return private

    # ssh-keygen handles all the format work (PEM, perms, OpenSSH magic).
    # ``-N ''`` empty passphrase: the file already lives under a per-user
    # tmpfs dir; an extra passphrase would have to be cached somewhere
    # less safe to unlock it on every transport call.
    subprocess.run(  # nosec B603 B607 — argv is a fixed list under our control
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-f",
            str(private),
            "-N",
            "",
            "-C",
            "krun-host (terok)",
            "-q",
        ],
        check=True,
    )
    return private


def set_runtime(runtime: ContainerRuntime) -> None:
    """Inject *runtime* as the process-wide handle (for tests)."""
    global _runtime
    _runtime = runtime


def reset_runtime() -> None:
    """Forget the cached runtime so the next ``get_runtime`` rebuilds from env."""
    global _runtime
    _runtime = None
