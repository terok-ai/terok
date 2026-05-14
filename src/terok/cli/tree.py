# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terok-side composition of the unified command tree.

Consumes the [`CommandTree`][terok_sandbox.commands.CommandTree]
exposed by terok-executor (which already has executor's vault
overlays applied + sandbox tree spliced under the ``sandbox`` node),
applies terok-specific overlays (terok's
[`SandboxConfig`][terok_sandbox.SandboxConfig] injected as ``cfg``
into handlers that take it), extends the vault group with terok's
local ``serve`` verb, and exposes the result both as deep paths and
as top-level shortcuts — identity-preserving so any terok wrap reaches
every entry point.

The wiring API is [`CommandTree`][terok_sandbox.commands.CommandTree]
from terok-sandbox; this module only contains the *composition*
specific to terok.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from typing import Any

from terok.lib.integrations.sandbox import CommandDef, CommandTree

_CFG_PARAM = "cfg"


def inject_cfg_factory(
    tree: CommandTree,
    *,
    subtree_paths: Sequence[tuple[str, ...]],
    factory: Callable[[], Any],
) -> CommandTree:
    """Overlay handlers under *subtree_paths* with a lazy ``cfg`` injector.

    For every leaf whose path is rooted at any of *subtree_paths* and
    whose handler declares a ``cfg`` parameter, the handler is wrapped
    so that ``cfg = factory()`` is supplied at call time when the caller
    omits it.  Other handlers (no ``cfg`` parameter) pass through
    untouched — sandbox-level ``ssh list`` etc. that don't want cfg
    aren't gratuitously wrapped.

    Identity is preserved for every untouched node so a shortcut that
    references the same modified subtree via [`find_at`][terok_sandbox.commands.CommandTree.find_at]
    sees the same wrap; both ``terok vault start`` (shortcut) and
    ``terok executor sandbox vault start`` (deep path) share the same
    wrapped handler when they reference the same
    [`CommandDef`][terok_sandbox.commands.CommandDef] instance.
    """
    overrides: dict[tuple[str, ...], Callable[..., Any]] = {}
    for path, cmd in tree.walk():
        if not any(path[: len(prefix)] == prefix for prefix in subtree_paths):
            continue
        if cmd.handler is None:
            continue
        sig = inspect.signature(cmd.handler)
        if _CFG_PARAM not in sig.parameters:
            continue
        overrides[path] = _wrap_with_cfg(cmd.handler, factory)
    return tree.overlay(overrides)


def _wrap_with_cfg(handler: Callable[..., Any], factory: Callable[[], Any]) -> Callable[..., Any]:
    """Return a wrapper that supplies ``cfg=factory()`` when the caller omits it.

    Lazy: ``factory`` is called per dispatch, not at overlay time, so
    a slow [`SandboxConfig`][terok_sandbox.SandboxConfig] build doesn't
    delay CLI startup.
    """

    @functools.wraps(handler)
    def wrapped(**kwargs: Any) -> Any:
        kwargs.setdefault(_CFG_PARAM, factory())
        return handler(**kwargs)

    return wrapped


__all__ = ["CommandDef", "CommandTree", "inject_cfg_factory"]
