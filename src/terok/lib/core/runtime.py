# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-project / per-call `ContainerRuntime` resolver.

Container-runtime selection is **per-project**, not process-global —
two projects in the same terok session can pick different OCI runtimes,
and the answer must be looked up at the call site that has the
project context in hand.

Selection priority, highest first:

1. ``TEROK_RUNTIME`` env var (``crun`` | ``krun`` | ``null``) — the
   override path used by ``terok task start`` invocations that want
   to force a runtime without editing config.
2. ``project.run.runtime`` from the project's ``project.yml`` — the
   per-project setting.
3. ``run.runtime`` from the user's global ``config.yml`` — the
   installation-wide default (for operators who want to put every
   project under krun in one place).
4. ``"crun"`` — the OCI runtime podman picks by default.

``krun`` requires the global ``experimental: true`` flag.  Without it,
selection raises ``SystemExit`` at startup with a clear pointer to the
opt-in — a misspelled env var or accidental config edit should never
silently boot the experimental backend.

All krun host-side provisioning (vault-backed ``%host`` keypair, tmpfs
materialisation, transport wiring) is delegated to
[`KrunHost`][terok_executor.KrunHost] in
terok-executor; terok flips the selector, executor does the rest.

For unit tests, mock this module's ``resolve_runtime`` directly rather
than swapping in a global — there's no longer any process-wide cache to
reset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from terok.lib.core.config import is_experimental, make_sandbox_config
from terok.lib.integrations.executor import KrunHost
from terok.lib.integrations.sandbox import (
    ContainerRuntime,
    NullRuntime,
    PodmanRuntime,
)

if TYPE_CHECKING:
    from terok.lib.core.project_model import ProjectConfig

RuntimeName = Literal["crun", "krun", "null"]
"""Accepted values for ``TEROK_RUNTIME`` and the resolver's internal
backend selector.  ``crun`` is the OCI-runtime name podman drives by
default; ``krun`` selects KVM-microVM isolation; ``null`` is the
in-memory stub for tests."""


def resolve_runtime(project: ProjectConfig | None = None) -> ContainerRuntime:
    """Return the [`ContainerRuntime`][terok_sandbox.ContainerRuntime] for *project*.

    See the module docstring for the full priority chain.  Constructs a
    fresh runtime per call — there is no process-wide cache — so two
    projects in the same session can pick different runtimes.  Cheap
    for podman (a no-op constructor); for krun, opens the vault to
    materialise the host keypair, so reserve repeated calls per task
    launch rather than per CLI invocation.

    Pass ``project=None`` for runtime-agnostic operations (image
    queries, cleanup, etc.) where the backend choice is invisible —
    those callers can also instantiate
    [`PodmanRuntime`][terok_sandbox.PodmanRuntime] directly and skip
    the resolver overhead.
    """
    backend = _select_backend(project)
    if backend == "crun":
        return PodmanRuntime()
    if backend == "null":
        return NullRuntime()
    if backend == "krun":
        if not is_experimental():
            raise SystemExit(
                "run.runtime: krun requires the experimental flag.  Set "
                "`experimental: true` in your config.yml, or pass "
                "`--experimental` on the command line."
            )
        return KrunHost(cfg=make_sandbox_config()).runtime()
    raise SystemExit(f"TEROK_RUNTIME={backend!r}: expected 'crun', 'krun', or 'null'")


def _select_backend(project: ProjectConfig | None) -> str:
    """Walk the priority chain and return the backend name.

    Separated so ``resolve_runtime`` reads top-down — the "what we end
    up running" decision lives in the lookups; the "how we construct
    it" decision lives in the dispatch below.
    """
    import os

    env_value = os.environ.get("TEROK_RUNTIME")
    if env_value is not None:
        return env_value.strip().lower()
    if project is not None and project.runtime is not None:
        return project.runtime
    return _global_runtime_default()


def _global_runtime_default() -> str:
    """Resolve the global ``run.runtime`` default from ``config.yml``.

    Falls through to ``"crun"`` when unset — that's the OCI runtime
    podman drives by default on every distro terok supports, so the
    fallback matches what an unconfigured host would do anyway.
    """
    from terok.lib.core.config import get_global_run_runtime

    return get_global_run_runtime() or "crun"
