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
import tempfile
from pathlib import Path

from terok.lib.core.config import is_experimental, make_sandbox_config
from terok.lib.integrations.sandbox import (
    ContainerRuntime,
    KrunRuntime,
    NullRuntime,
    PodmanRuntime,
    VsockSSHTransport,
    ensure_infra_keypair,
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
    """Materialise the vault-backed ``%host`` keypair onto a tmpfs file
    that ``ssh -i`` can read.

    The vault is the system of record: the keypair lives in the sandbox
    credential DB under the ``%host`` infrastructure scope.  This
    helper opens the DB, calls
    [`ensure_infra_keypair`][terok_sandbox.ensure_infra_keypair] (which
    generates the key on first call and reloads it thereafter), and
    writes the OpenSSH-PEM private + the public-key line into
    *runtime_dir* (default:
    [`namespace_runtime_dir()`][terok_sandbox.namespace_runtime_dir]).

    Rotation = clear the ``%host`` scope in the vault, then re-run.
    Called once per process from ``_build_krun_runtime`` (the runtime
    handle is cached by ``get_runtime``), so the tmpfs cache lasts
    for the process lifetime; an out-of-band rotation propagates the
    next time terok starts.  The public half (``krun_host.key.pub``)
    must be baked into the L0G guest image at build time so the guest
    accepts our auth.

    Requires the vault to be unlocked — the krun runtime is gated on
    ``experimental: true`` and assumes the operator has the vault
    open for the session.  A ``NoPassphraseError`` propagates as
    ``SystemExit`` with a pointer at the unlock verb.
    """
    target_dir = _ensure_safe_runtime_dir(runtime_dir)
    private = target_dir / f"{_HOST_KEYPAIR_BASENAME}.key"
    public = target_dir / f"{_HOST_KEYPAIR_BASENAME}.key.pub"

    db = make_sandbox_config().open_credential_db(prompt_on_tty=False)
    try:
        infra = ensure_infra_keypair("%host", db=db, comment="krun-host (terok)")
    finally:
        db.close()

    _write_atomic(private, infra.private_pem, mode=0o600)
    _write_atomic(public, (infra.public_line + "\n").encode(), mode=0o644)
    return private


def _ensure_safe_runtime_dir(runtime_dir: Path | None) -> Path:
    """Resolve the krun runtime dir and refuse persistent-disk fallbacks.

    ``namespace_runtime_dir()`` falls back to ``$XDG_STATE_HOME/terok``
    (persistent disk) when ``$XDG_RUNTIME_DIR`` is unset.  Writing
    plaintext private-key material to persistent disk is the exact
    "vault → disk" leak the vault-backed flow was supposed to prevent,
    so refuse the fallback when no explicit *runtime_dir* is given.

    Caller-supplied paths are trusted (tests, operator overrides).
    The chmod after ``mkdir`` is unconditional because ``mkdir(mode=…,
    exist_ok=True)`` is a no-op for an existing dir — a previous run
    under a more permissive umask could otherwise leave the cache dir
    world-listable.
    """
    if runtime_dir is not None:
        target = runtime_dir
    else:
        if not os.environ.get("XDG_RUNTIME_DIR"):
            raise SystemExit(
                "krun host-key cache requires $XDG_RUNTIME_DIR (a tmpfs "
                "user-runtime dir) to be set so the vault-backed private "
                "key never lands on persistent disk.  Run terok under a "
                "logind-managed session (the usual interactive shell), "
                "or set XDG_RUNTIME_DIR to a tmpfs path before launching."
            )
        target = namespace_runtime_dir()

    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target, 0o700)
    return target


def _write_atomic(path: Path, data: bytes, *, mode: int) -> None:
    """Write *data* to *path* atomically with *mode* perms.

    Uses ``mkstemp`` (``O_EXCL`` — symlinks at the target are ignored)
    + ``fchmod`` on the descriptor + ``os.replace`` for the rename, so
    there's no TOCTOU window where an attacker could swap in a
    hardlink between the write and a chmod-by-path.  No ``fsync``:
    the path lives under a tmpfs (enforced by
    [`_ensure_safe_runtime_dir`][terok.lib.core.runtime._ensure_safe_runtime_dir])
    where it would be a no-op cost.
    """
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp_path, path)


def set_runtime(runtime: ContainerRuntime) -> None:
    """Inject *runtime* as the process-wide handle (for tests)."""
    global _runtime
    _runtime = runtime


def reset_runtime() -> None:
    """Forget the cached runtime so the next ``get_runtime`` rebuilds from env."""
    global _runtime
    _runtime = None
