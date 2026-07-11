# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terok-side composition of the unified command tree.

Consumes the [`CommandTree`][terok_util.cli_types.CommandTree]
exposed by terok-executor (which already has executor's vault
overlays applied + sandbox tree spliced under the ``sandbox`` node),
applies terok-specific overlays (terok's
[`SandboxConfig`][terok_sandbox.SandboxConfig] injected as ``cfg``
into handlers that take it), extends the vault group with terok's
local ``serve`` verb, and exposes the result both as deep paths and
as top-level shortcuts — identity-preserving so any terok wrap reaches
every entry point.

The wiring API is [`CommandTree`][terok_util.cli_types.CommandTree]
from terok-sandbox; this module only contains the *composition*
specific to terok.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from typing import Any

from terok_util import LazyHandler

from terok.lib.api import CommandDef, CommandTree

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
    references the same modified subtree via [`find_at`][terok_util.cli_types.CommandTree.find_at]
    sees the same wrap; both ``terok vault start`` (shortcut) and
    ``terok executor sandbox vault start`` (deep path) share the same
    wrapped handler when they reference the same
    [`CommandDef`][terok_util.cli_types.CommandDef] instance.
    """
    overrides: dict[tuple[str, ...], Callable[..., Any]] = {}
    for path, cmd in tree.walk():
        if not any(path[: len(prefix)] == prefix for prefix in subtree_paths):
            continue
        if cmd.handler is None:
            continue
        # Sibling registries wire handlers as opaque LazyHandler("mod:fn");
        # inspecting one directly sees only its __call__(*args, **kwargs),
        # never the real `cfg` parameter.  Resolve it — this subtree is
        # behind the wired-tree gate, so we only get here on a sibling verb
        # that loads the handler anyway.
        target = cmd.handler.resolve() if isinstance(cmd.handler, LazyHandler) else cmd.handler
        sig = inspect.signature(target)
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


def inject_pt_resolver(
    tree: CommandTree,
    *,
    verb_specs: Sequence[tuple[tuple[str, ...], str]],
) -> CommandTree:
    """Overlay listed verbs so a positional accepts `project/task` or raw container id.

    For each ``(path, kwarg)`` entry, wraps the handler at *path* so
    that, if the named keyword argument is a string containing ``/``,
    it's split on the first slash, treated as ``(project_name,
    task_id)``, and resolved to the task's current container name via
    [`lookup_container_by_pt`][terok.lib.orchestration.tasks.query.lookup_container_by_pt].
    Inputs without ``/`` (raw container ids) pass through untouched —
    same handler, same kwargs.

    ``/`` is the disambiguator because it's invalid in podman
    container names and in any sensible project or task slug.  The
    convention follows git's dual-form precedent (``origin master``
    vs ``origin/master``) and Docker-Compose's "service vs container"
    split.
    """
    overrides: dict[tuple[str, ...], Callable[..., Any]] = {}
    for path, kwarg in verb_specs:
        cmd = tree.find_at(path)
        if cmd is None or cmd.handler is None:
            continue
        overrides[path] = _wrap_with_pt_resolver(cmd.handler, kwarg)
    return tree.overlay(overrides)


def _wrap_with_pt_resolver(handler: Callable[..., Any], kwarg: str) -> Callable[..., Any]:
    """Return a wrapper that resolves ``project/task`` in *kwarg* before delegating.

    The import of
    [`lookup_container_by_pt`][terok.lib.orchestration.tasks.query.lookup_container_by_pt]
    is deferred to call time so tests can substitute it via
    ``patch`` at the canonical location and the resolver picks up the
    substitution on the next dispatch.
    """

    @functools.wraps(handler)
    def wrapped(**kwargs: Any) -> Any:
        value = kwargs.get(kwarg)
        if isinstance(value, str) and "/" in value:
            from terok.lib.orchestration.tasks import lookup_container_by_pt

            project, _, task = value.partition("/")
            resolved = lookup_container_by_pt(project, task)
            if resolved is not None:
                kwargs[kwarg] = resolved
        return handler(**kwargs)

    return wrapped


__all__ = ["CommandDef", "CommandTree", "inject_cfg_factory", "inject_pt_resolver"]
