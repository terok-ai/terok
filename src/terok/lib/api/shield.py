# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield operations and CLI registry — public API surface.

Re-export catalog for the egress-firewall layer.  Sources:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] for
the high-level shield surface terok-sandbox owns
([`ShieldManager`][terok_sandbox.ShieldManager],
[`ShieldHooks`][terok_sandbox.ShieldHooks],
[`RecoveryStatus`][terok_sandbox.RecoveryStatus]), and
[`terok.lib.integrations.shield`][terok.lib.integrations.shield] for
the lower-level CLI registry (``COMMANDS``, ``ArgDef``, ``CommandDef``,
``ExecError``) that terok's ``terok shield`` bridge wires into its own
command tree.

shield's ``CommandDef`` is aliased to ``ShieldCommandDef`` so it doesn't
collide with sandbox's ``CommandDef`` (which already flows through
[`terok.lib.api`][terok.lib.api] for the CLI tree).
"""

from pathlib import Path

from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    RecoveryStatus,
    SandboxConfig,
    ShieldHooks,
    ShieldManager,
    installed_versions,
    read_stamp,
    stamp_path,
)
from terok.lib.integrations.shield import (  # noqa: F401 — re-exported public API
    COMMANDS as SHIELD_COMMANDS,
    ArgDef,
    CommandDef as ShieldCommandDef,
    ExecError,
    needs_container as shield_needs_container,
    standalone_only as shield_standalone_only,
)

# ── Thin shims wrapping the post-W5.B class API ─────────
#
# The free-function names terok's callers (TUI, CLI, tests) reach
# for predate the ShieldManager / ShieldHooks consolidation in
# terok-sandbox.  Kept as one-liner pass-throughs so this api/* layer
# remains a stable contract while the underlying class API evolves.


def make_shield(task_dir: Path, cfg: SandboxConfig | None = None) -> object:
    """Build a Shield for *task_dir* — shim around ``ShieldManager(...).shield``."""
    return ShieldManager(task_dir, cfg).shield


def shield_up(cname: str, task_dir: Path, cfg: SandboxConfig | None = None) -> None:
    """Bring this task's shield up — shim around ``ShieldManager.up``."""
    ShieldManager(task_dir, cfg).up(cname)


def shield_down(
    cname: str, task_dir: Path, cfg: SandboxConfig | None = None, *, allow_all: bool = False
) -> None:
    """Drop this task's shield — shim around ``ShieldManager.down``."""
    ShieldManager(task_dir, cfg).down(cname, allow_all=allow_all)


def shield_state(cname: str, task_dir: Path, cfg: SandboxConfig | None = None) -> object:
    """Live shield state — shim around ``ShieldManager.state``."""
    return ShieldManager(task_dir, cfg).state(cname)


def shield_status(cfg: SandboxConfig | None = None) -> dict:
    """Shield status — shim around ``ShieldManager(tmp).status``.

    Status is config-level only; the throwaway task_dir is never written to.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        return ShieldManager(Path(tmp), cfg).status()


def shield_run_setup(*, root: bool = False, user: bool = False) -> None:
    """Install global OCI hooks — shim around ``ShieldHooks.install``."""
    ShieldHooks.install(root=root, user=user)


__all__ = [
    "ArgDef",
    "ExecError",
    "RecoveryStatus",
    "SHIELD_COMMANDS",
    "ShieldCommandDef",
    "ShieldHooks",
    "ShieldManager",
    "shield_needs_container",
    "shield_standalone_only",
    "installed_versions",
    "make_shield",
    "read_stamp",
    "shield_down",
    "shield_run_setup",
    "shield_state",
    "shield_status",
    "shield_up",
    "stamp_path",
]
