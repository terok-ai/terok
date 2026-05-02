# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task lifecycle hook execution and tracking.

Runs user-configured shell commands at task lifecycle points on the host.
Hook commands receive task context via environment variables.  Tracks
which hooks have fired in task metadata so sickbay can detect and
reconcile missed hooks (e.g. post_stop after an unclean shutdown).
"""

from __future__ import annotations

import logging
import os
import subprocess  # nosec B404 — hooks execute user-configured commands by design
from pathlib import Path

logger = logging.getLogger(__name__)

_STARTUP_HOOK_TIMEOUT = 120  # seconds for pre_start / post_start / post_ready
_STOP_HOOK_TIMEOUT = 30  # seconds for post_stop

#: Hooks that fire during the task lifecycle.
HOOK_NAMES = ("pre_start", "post_start", "post_ready", "post_stop")


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


def _record_hook(dossier_path: Path, hook_name: str) -> None:
    """Append *hook_name* to the ``hooks_fired`` list in task bookkeeping.

    *dossier_path* is the dossier-file handle the caller already has
    (returned by ``load_task_meta`` / ``_dossier_path``).  ``hooks_fired``
    is bookkeeping (not wire dossier), so the actual write targets the
    sibling ``_meta.yml`` file.  Atomic-rename keeps an interrupted
    record from leaving a torn YAML behind.
    """
    yml_path = _bookkeeping_yml_for(dossier_path)
    if not yml_path.is_file():
        # No bookkeeping file yet (task hasn't been written by terok)
        # — nothing to update.  The hook still ran; we just don't have
        # a place to record the marker.
        return
    try:
        from ..util.yaml import dump as _yaml_dump, load as _yaml_load

        meta = _plain(_yaml_load(yml_path.read_text(encoding="utf-8")) or {})
        fired = meta.get("hooks_fired") or []
        if hook_name not in fired:
            fired.append(hook_name)
        meta["hooks_fired"] = fired
        tmp = yml_path.with_suffix(yml_path.suffix + ".tmp")
        tmp.write_text(_yaml_dump(meta), encoding="utf-8")
        os.replace(tmp, yml_path)
    except Exception:
        logger.warning("failed to record hook %s in %s", hook_name, yml_path, exc_info=True)


#: Suffixes the hook recorder recognises on a dossier-file handle —
#: kept inline rather than imported from ``tasks.py`` because that
#: module imports ``run_hook`` from here and the reverse would build
#: a cycle.  The canonical (``_dossier.json``) and pre-self-describing
#: (``.json``) layouts both map to the bookkeeping YAML alongside.
_DOSSIER_SUFFIXES = ("_dossier.json", ".json")
_BOOKKEEPING_SUFFIXES = ("_meta.yml", ".yml")


def _bookkeeping_yml_for(dossier_path: Path) -> Path:
    """Return the ``_meta.yml`` sibling of *dossier_path*.

    Falls back to the legacy ``.yml`` location if it's the one on disk
    — a hook firing mid-migration shouldn't lose its record just
    because the rename hasn't happened yet.
    """
    name = dossier_path.name
    for src in _DOSSIER_SUFFIXES:
        if name.endswith(src):
            stem = name[: -len(src)]
            new = dossier_path.parent / f"{stem}{_BOOKKEEPING_SUFFIXES[0]}"
            if new.is_file():
                return new
            legacy = dossier_path.parent / f"{stem}{_BOOKKEEPING_SUFFIXES[1]}"
            return legacy if legacy.is_file() else new
    # Unknown dossier suffix — best-effort sibling under the dossier
    # path's stem with the canonical bookkeeping suffix.  Used when a
    # caller hands us a path that doesn't match either layout (tests
    # constructing toy paths, defensive code paths in callers).
    return dossier_path.parent / f"{dossier_path.stem}{_BOOKKEEPING_SUFFIXES[0]}"


def _plain(obj: object) -> object:
    """Recursively unwrap commented containers to plain dict/list."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


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
    meta_path: Path | None = None,
) -> None:
    """Execute a lifecycle hook command if configured.

    The command is run via ``sh -c`` with task context in environment
    variables.  Errors are logged as warnings — hooks must not break the
    task lifecycle.

    If *meta_path* is provided, the hook name is recorded in the task's
    ``hooks_fired`` metadata list (even when *command* is None — the hook
    point was reached, so it counts as "fired").
    """
    # Always record that this hook point was reached, even if no command
    if meta_path:
        _record_hook(meta_path, hook_name)

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

    timeout = _STOP_HOOK_TIMEOUT if hook_name == "post_stop" else _STARTUP_HOOK_TIMEOUT
    try:
        result = subprocess.run(  # nosec B603 B607
            ["sh", "-c", command],
            env=env,
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.debug("hook %s stdout: %s", hook_name, result.stdout.rstrip())
        if result.stderr:
            logger.debug("hook %s stderr: %s", hook_name, result.stderr.rstrip())
    except subprocess.TimeoutExpired:
        logger.warning("hook %s timed out after %ds", hook_name, timeout)
    except Exception:
        logger.warning("hook %s failed", hook_name, exc_info=True)
