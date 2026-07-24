# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Interactive CLI task runner.

``task_run_cli`` launches a detached CLI-mode container and waits for
its readiness marker before printing login instructions.  It only ever
creates: bringing an existing container back is ``task restart``'s job
([`ensure_task_running`][terok.lib.orchestration.task_runners.restart.ensure_task_running]).
"""

from __future__ import annotations

from datetime import UTC, datetime

from terok.lib.integrations.executor import resolve_agent_value
from terok.lib.integrations.sandbox import Sharing, VolumeSpec

from ...core import runtime as _rt
from ...core.images import project_cli_image
from ...core.projects import load_project
from ...util.ansi import green as _green, red as _red, supports_color as _supports_color
from ...util.logging_utils import _log_debug, timed_phase
from ..agent_config import resolve_agent_config
from ..environment import build_task_env_and_volumes
from ..hooks import run_hook
from ..tasks import (
    CONTAINER_TEROK_CONFIG,
    container_name,
    load_task_meta,
    write_task_meta,
)
from .config import _apply_unrestricted_env, _prepare_agent_config, _str_to_bool
from .container import (
    _assert_running,
    _print_login_instructions,
    _refuse_existing_container,
    _run_container,
)
from .shield import _apply_shield_policy


def task_run_cli(
    project_name: str,
    task_id: str,
    unrestricted: bool | None = None,
    debug: bool = False,
) -> None:
    """Launch a CLI-mode task container and wait for its readiness marker.

    Creates a detached Podman container for interactive CLI access.
    After the container reports ready the task metadata is marked
    ``running`` and the user is shown login instructions.
    """
    project = load_project(project_name)
    meta, meta_path = load_task_meta(project.name, task_id, "cli")

    cname = container_name(project.name, "cli", task_id)
    _log_debug(f"cli run: start project={project.name} task={task_id} cname={cname}")
    # One resolve per launch — vault-expensive under krun.
    runtime = _rt.resolve_runtime(project)
    _refuse_existing_container(runtime, project.name, cname, task_id)

    env, volumes, egress = build_task_env_and_volumes(project, task_id)

    # Resolve layered agent config (global → project → CLI overrides)
    agent_config_dir = _prepare_agent_config(project, project_name, task_id)
    volumes.append(VolumeSpec(agent_config_dir, CONTAINER_TEROK_CONFIG, sharing=Sharing.PRIVATE))

    # Resolve unrestricted mode: CLI flag → config → default (True)
    if unrestricted is None:
        _effective = resolve_agent_config(
            project_name,
            agent_config=project.agent_config,
            project_root=project.root,
        )
        _cfg_val = resolve_agent_value(
            "unrestricted", _effective, project.default_agent or "claude"
        )
        unrestricted = _cfg_val is None or _str_to_bool(_cfg_val)
    if unrestricted:
        _apply_unrestricted_env(env)

    # Run detached and keep the container alive so users can exec into it later
    # Note: We intentionally do NOT use --rm so containers persist after stopping.
    # This allows `task restart` to quickly resume stopped containers.
    task_dir = project.tasks_root / str(task_id)
    run_hook(
        "pre_start",
        project.hook_pre_start,
        project_name=project.name,
        task_id=task_id,
        mode="cli",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _run_container(
        cname=cname,
        image=project_cli_image(project.name),
        env=env,
        volumes=volumes,
        project=project,
        task_id=task_id,
        task_dir=task_dir,
        # Ensure init runs and then keep the container alive even without a TTY
        # init-ssh-and-repo.sh now prints a readiness marker we can watch for
        command=["bash", "-lc", "init-ssh-and-repo.sh && echo __CLI_READY__; tail -f /dev/null"],
        allow_debugger=debug,
        egress=egress,
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=False)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_name=project.name,
        task_id=task_id,
        mode="cli",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    # Stream initial logs until ready marker is seen (or timeout), then detach
    with timed_phase(f"launch[{cname}]: stream init logs"):
        runtime.container(cname).stream_initial_logs(
            ready_check=lambda line: "__CLI_READY__" in line or ">> init complete" in line,
            timeout_sec=60.0,
        )

    # Verify the container is still alive after log streaming
    _assert_running(project, cname)
    run_hook(
        "post_ready",
        project.hook_post_ready,
        project_name=project.name,
        task_id=task_id,
        mode="cli",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    meta["mode"] = "cli"
    meta["ready_at"] = datetime.now(UTC).isoformat()
    meta["unrestricted"] = unrestricted
    meta["debug"] = debug
    write_task_meta(meta_path, meta)

    color_enabled = _supports_color()
    print(
        f"\nCLI container is running in the background.\n- Name:     {_green(cname, color_enabled)}"
    )
    _print_login_instructions(project.name, task_id, cname, color_enabled)
    print(f"- To stop:  {_red(f'podman stop {cname}', color_enabled)}\n")


__all__ = [
    "task_run_cli",
]
