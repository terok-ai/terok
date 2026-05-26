# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task container runners: CLI, headless, toad, and restart.

This package is the single import surface for task-runner operations.
The implementation is split per mode and per concern:

* [`container`][terok.lib.orchestration.task_runners.container] —
  podman launch + lifecycle primitives (``_run_container``,
  ``_podman_start``, …) shared by every runner.
* [`shield`][terok.lib.orchestration.task_runners.shield] — the
  per-task egress-firewall policy applied after a container starts.
* [`config`][terok.lib.orchestration.task_runners.config] —
  agent-config assembly and unrestricted-mode resolution.
* [`cli`][terok.lib.orchestration.task_runners.cli] — interactive
  CLI-mode runner.
* [`toad`][terok.lib.orchestration.task_runners.toad] — the Caddy-gated
  web-TUI runner.
* [`headless`][terok.lib.orchestration.task_runners.headless] —
  autopilot run + follow-up runners and their request value objects.
* [`restart`][terok.lib.orchestration.task_runners.restart] — stop +
  start an existing task container.

Task metadata, lifecycle, and queries live in the companion ``tasks``
package.
"""

from .cli import task_run_cli  # noqa: F401 — re-exported public API
from .headless import (  # noqa: F401 — re-exported public API
    DetachedSummary,
    HeadlessRunRequest,
    task_followup_headless,
    task_run_headless,
)
from .restart import task_restart  # noqa: F401 — re-exported public API
from .shield import resolve_container_uuid  # noqa: F401 — re-exported public API
from .toad import task_run_toad  # noqa: F401 — re-exported public API

__all__ = [
    "DetachedSummary",
    "HeadlessRunRequest",
    "resolve_container_uuid",
    "task_followup_headless",
    "task_restart",
    "task_run_cli",
    "task_run_headless",
    "task_run_toad",
]
