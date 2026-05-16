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
silently boot the experimental backend.  All krun host-side
provisioning (vault-backed ``%host`` keypair, tmpfs materialisation,
transport wiring) is delegated to
[`make_krun_runtime`][terok_executor.make_krun_runtime] in
terok-executor — terok flips the selector, executor does the rest.

Tests that need a specific backend should call [`set_runtime`][terok.lib.core.runtime.set_runtime]
in setup and [`reset_runtime`][terok.lib.core.runtime.reset_runtime] in teardown.
"""

from __future__ import annotations

import os

from terok.lib.core.config import is_experimental, make_sandbox_config
from terok.lib.integrations.executor import make_krun_runtime
from terok.lib.integrations.sandbox import (
    ContainerRuntime,
    KrunRuntime,
    NullRuntime,
    PodmanRuntime,
)

_runtime: ContainerRuntime | None = None


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
    """Gate on the experimental flag, then defer to executor's krun factory.

    The host-side keypair (``%host`` infrastructure scope), the tmpfs
    materialisation, the vsock-SSH transport, and the
    [`KrunRuntime`][terok_sandbox.KrunRuntime] construction itself all
    live in [`terok_executor.krun`][terok_executor.krun].  Keeping
    them out of terok is intentional: anything that owns
    ``build_l0g_image`` also owns the trust material that makes the
    resulting guest reachable, so the executor is the right home.
    """
    if not is_experimental():
        raise SystemExit(
            "TEROK_RUNTIME=krun requires the experimental flag.  Set "
            "`experimental: true` in your config.yml, or pass "
            "`--experimental` on the command line."
        )
    return make_krun_runtime(cfg=make_sandbox_config())


def set_runtime(runtime: ContainerRuntime) -> None:
    """Inject *runtime* as the process-wide handle (for tests)."""
    global _runtime
    _runtime = runtime


def reset_runtime() -> None:
    """Forget the cached runtime so the next ``get_runtime`` rebuilds from env."""
    global _runtime
    _runtime = None
