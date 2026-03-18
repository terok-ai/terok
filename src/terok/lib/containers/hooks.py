# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle hook execution.

Runs user-configured shell commands at task lifecycle points on the host.
Hook commands receive task context via environment variables.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_HOOK_TIMEOUT = 30  # seconds for synchronous hooks (pre_stop)


def _build_hook_env(
    project_id: str,
    task_id: str,
    mode: str,
    cname: str,
    hook_name: str,
    *,
    web_port: int | None = None,
    task_dir: Path | None = None,
) -> dict[str, str]:
    """Build the environment dict passed to hook commands."""
    env = {
        **os.environ,
        "TEROK_HOOK": hook_name,
        "TEROK_PROJECT_ID": project_id,
        "TEROK_TASK_ID": str(task_id),
        "TEROK_TASK_MODE": mode,
        "TEROK_CONTAINER_NAME": cname,
    }
    if web_port is not None:
        env["TEROK_WEB_PORT"] = str(web_port)
    if task_dir is not None:
        env["TEROK_TASK_DIR"] = str(task_dir)
    return env


def run_hook(
    hook_name: str,
    command: str | None,
    *,
    project_id: str,
    task_id: str,
    mode: str,
    cname: str,
    web_port: int | None = None,
    task_dir: Path | None = None,
) -> None:
    """Execute a lifecycle hook command if configured.

    The command is run via ``sh -c`` with task context in environment
    variables.  Errors are logged as warnings — hooks must not break the
    task lifecycle.

    For ``pre_stop``, the command runs synchronously with a timeout.
    For ``post_start`` and ``post_ready``, it also runs synchronously
    but failures are non-fatal.
    """
    if not command:
        return

    env = _build_hook_env(
        project_id,
        task_id,
        mode,
        cname,
        hook_name,
        web_port=web_port,
        task_dir=task_dir,
    )

    logger.debug("hook %s: running %r", hook_name, command)

    timeout = _HOOK_TIMEOUT if hook_name == "pre_stop" else None
    try:
        subprocess.run(
            ["sh", "-c", command],
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("hook %s timed out after %ds", hook_name, _HOOK_TIMEOUT)
    except Exception:
        logger.warning("hook %s failed", hook_name, exc_info=True)
