# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container-based command execution for agent workspaces.

Runs git (and other commands) **inside** task containers via the
runtime ``exec`` API instead of on the host, eliminating the risk of
poisoned git hooks or scripts executing with host privileges.
"""

from subprocess import TimeoutExpired

from terok.lib.integrations.sandbox import Sandbox

from ..core import runtime as _rt
from ..core.config import make_sandbox_config
from ..core.projects import load_project
from ..core.task_state import container_name as _container_name
from ..util.logging_utils import _log_debug


def container_git_diff(
    project_name: str,
    task_id: str,
    mode: str,
    *args: str,
    timeout: int = 30,
) -> str | None:
    """Run ``git diff`` inside a task container and return stdout.

    Args:
        project_name: Project identifier.
        task_id: Task identifier.
        mode: Container mode (``"cli"``, ``"web"``, ``"run"``, ``"toad"``).
        *args: Additional arguments passed to ``git diff`` (e.g.
            ``"--stat"``, ``"HEAD@{1}..HEAD"``).
        timeout: Subprocess timeout in seconds.

    Returns:
        The diff output on success, or ``None`` on any failure.

    If the container is stopped/exited it is temporarily restarted for the
    exec, then stopped again.  All git commands target the container-internal
    ``/workspace`` path — the host ``workspace-dangerous`` path is never
    passed to any subprocess.
    """
    project = load_project(project_name)
    runtime = _rt.resolve_runtime(project)
    cname = _container_name(project_name, mode, task_id)
    container = runtime.container(cname)
    state = container.state

    if state is None:
        _log_debug(f"container_git_diff: container {cname} not found")
        return None

    restarted = False
    if state != "running":
        if mode == "run":
            # Never restart exited headless containers — podman start replays
            # the original entrypoint (the agent command), causing duplicate
            # commits, network calls, and other side effects.
            _log_debug(f"container_git_diff: refusing to restart exited headless container {cname}")
            return None
        # Start through the sandbox facade so it rebuilds the /run/terok
        # bind-mount source (wiped by a reboot, removed by the supervisor
        # on every stop) — this temporary restart need not know that
        # host-side precondition.
        try:
            Sandbox(make_sandbox_config(), runtime=runtime).start(cname)
        except (FileNotFoundError, RuntimeError) as exc:
            _log_debug(f"container_git_diff: temporary start({cname}) failed: {exc}")
            return None
        restarted = True

    try:
        result = runtime.exec(
            container, ["git", "-C", "/workspace", "diff", *args], timeout=timeout
        )
        if not result.ok:
            _log_debug(f"container_git_diff: git diff failed rc={result.exit_code}")
            return None
        return result.stdout
    except (FileNotFoundError, TimeoutExpired) as exc:
        _log_debug(f"container_git_diff: {exc}")
        return None
    finally:
        if restarted:
            try:
                container.stop(timeout=10)
            except (FileNotFoundError, RuntimeError):
                pass
