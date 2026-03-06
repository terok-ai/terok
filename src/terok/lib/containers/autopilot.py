# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Autopilot container lifecycle helpers.

Encapsulates the podman-level operations for headless/autopilot containers
so that the TUI (presentation layer) doesn't need raw subprocess calls.
"""

import subprocess

from .tasks import update_task_exit_code


def wait_for_container_exit(
    container_name: str,
    project_id: str,
    task_id: str,
    timeout: int = 7200,
) -> tuple[int | None, str | None]:
    """Wait for a container to exit and update task metadata.

    Returns ``(exit_code, error_message)``.  On success *error_message* is
    ``None``; on failure *exit_code* is ``None``.
    """
    try:
        result = subprocess.run(
            ["podman", "wait", container_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            return None, f"podman wait failed: {err}"

        try:
            exit_code = int(result.stdout.strip())
        except ValueError:
            return None, f"podman wait returned non-integer: {result.stdout.strip()!r}"

        update_task_exit_code(project_id, task_id, exit_code)
        return exit_code, None
    except subprocess.TimeoutExpired:
        return None, "Watcher timed out"
    except Exception as e:
        return None, str(e)


def follow_container_logs_cmd(container_name: str) -> list[str]:
    """Return the podman command to follow container logs."""
    return ["podman", "logs", "-f", container_name]
