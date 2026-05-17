# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Toad web-TUI task runner.

``task_run_toad`` launches the Toad multi-agent TUI behind Caddy for
token-gated browser access; ``_resume_toad_container`` is the fast path
for a toad task whose container already exists.  The token/URL helpers
(``_ensure_toad_token``, ``_toad_browser_url``, ``_rehydrate_toad_token``)
are also consumed by the restart runner.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from terok.lib.integrations.executor import resolve_provider_value
from terok.lib.integrations.sandbox import Sharing, VolumeSpec

from ...core import runtime as _rt
from ...core.config import get_public_host
from ...core.images import project_cli_image
from ...core.projects import load_project
from ...util.ansi import (
    blue as _blue,
    green as _green,
    hyperlink as _hyperlink,
    red as _red,
    supports_color as _supports_color,
    yellow as _yellow,
)
from ...util.net import url_host
from ..agent_config import resolve_agent_config
from ..environment import build_task_env_and_volumes, ensure_vault
from ..hooks import run_hook
from ..ports import assign_web_port, release_web_port
from ..tasks import (
    CONTAINER_TEROK_CONFIG,
    container_name,
    load_task_meta,
    write_task_meta,
)
from .config import _apply_unrestricted_env, _prepare_agent_config, _str_to_bool
from .container import _assert_running, _podman_start, _run_container
from .shield import _apply_shield_policy

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig

_LOCALHOST = "127.0.0.1"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TOAD_PUBLIC_PORT = 8080
"""Port that Caddy binds inside the container — the one podman publishes."""
_TOAD_INTERNAL_PORT = 8081
"""Loopback port that toad binds inside the container — reached only via Caddy."""
_TOAD_TOKEN_FILE_NAME = "toad.token"  # nosec B105 — filename, not a credential


def _ensure_toad_token(agent_config_dir: Path, existing: str | None = None) -> str:
    """Per-task auth token for Caddy, written 0600 to ``toad.token`` and returned.

    Reuses *existing* (restart path) or mints a fresh 32-byte urlsafe
    string.  The write goes through a same-directory temp file + atomic
    ``os.replace``: ``agent-config`` is a bind mount, and a stopped
    container could pre-stage a symlink *or a hardlink* at
    ``toad.token`` — ``O_NOFOLLOW`` protects against the former, but
    only a rename that never touches the destination inode protects
    against the latter (truncating a hardlink clobbers the peer's
    content).
    """
    token = existing or secrets.token_urlsafe(32)
    dir_fd = os.open(agent_config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    tmp_name = f".{_TOAD_TOKEN_FILE_NAME}.{secrets.token_hex(8)}"
    try:
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
        try:
            os.replace(tmp_name, _TOAD_TOKEN_FILE_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            # Clean up the temp file if replace failed for any reason.
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            raise
    finally:
        os.close(dir_fd)
    return token


def _toad_browser_url(public_host: str, port: int, token: str) -> str:
    """Return the first-hit URL that seeds the Caddy-set auth cookie."""
    return f"http://{url_host(public_host)}:{port}/?token={token}"


def _agent_config_dir(project: ProjectConfig, task_id: str) -> Path:
    """Return the agent-config mount path for *task_id* under *project*."""
    return project.tasks_root / str(task_id) / "agent-config"


def _rehydrate_toad_token(project: ProjectConfig, task_id: str, meta: dict, cname: str) -> str:
    """Saved toad token from *meta*, rewritten to ``agent-config/toad.token``.

    The file may have been cleaned up between runs even when the token
    persists in metadata; rewriting on every reuse is cheap insurance.
    """
    saved_token = meta.get("web_token")
    if not isinstance(saved_token, str):
        raise SystemExit(
            f"Existing toad container {cname} has no saved web_token in metadata "
            f"(created before the Caddy auth gate landed).  Re-create the task."
        )
    _ensure_toad_token(_agent_config_dir(project, task_id), existing=saved_token)
    return saved_token


def _resume_toad_container(
    *,
    project: ProjectConfig,
    task_id: str,
    cname: str,
    container_state: str,
    meta: dict,
    meta_path: Path,
    pub_host: str,
) -> None:
    """Fast-path for a toad task whose container already exists: rehydrate the token, start it if stopped, print the URL."""
    saved_port = meta.get("web_port")
    if not isinstance(saved_port, int):
        raise SystemExit(f"Existing toad container {cname} has no saved web_port in metadata.")
    actual = assign_web_port(project.id, task_id, preferred=saved_port)
    if actual != saved_port:
        # The registry handed us a fallback port — release it so the task
        # doesn't leak a claim we'll never publish.
        release_web_port(project.id, task_id)
        raise SystemExit(
            f"Port {saved_port} for {project.id}/{task_id} is no longer available "
            f"(got {actual}).  Re-create the task to use the new port."
        )
    ensure_vault()
    saved_token = _rehydrate_toad_token(project, task_id, meta, cname)
    color_enabled = _supports_color()
    url = _toad_browser_url(pub_host, saved_port, saved_token)
    if container_state == "running":
        print(f"Container {_green(cname, color_enabled)} is already running.")
        print(f"Toad: {_hyperlink(_blue(url, color_enabled), url, enabled=color_enabled)}")
        return
    print(f"Starting existing container {_green(cname, color_enabled)}...")
    task_dir = project.tasks_root / str(task_id)
    _podman_start(project, cname)
    _assert_running(project, cname)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=saved_port,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=True)
    print("Container started.")
    print(f"Toad: {_hyperlink(_blue(url, color_enabled), url, enabled=color_enabled)}")


def task_run_toad(
    project_id: str,
    task_id: str,
    agents: list[str] | None = None,
    preset: str | None = None,
    unrestricted: bool | None = None,
) -> None:
    """Launch the Toad multi-agent TUI behind Caddy for token-gated browser access.

    Same CLI image as interactive tasks, but the container entrypoint is
    ``terok-toad-entry``: it starts Caddy on the published port, toad on
    an internal loopback port, and emits ``TEROK_READY`` once both are
    listening.  Caddy enforces the per-task token (see
    `_ensure_toad_token`) on every request.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id, "toad")

    cname = container_name(project.id, "toad", task_id)
    container_state = _rt.resolve_runtime(project).container(cname).state

    pub_host = get_public_host()

    if container_state is not None:
        _resume_toad_container(
            project=project,
            task_id=task_id,
            cname=cname,
            container_state=container_state,
            meta=meta,
            meta_path=meta_path,
            pub_host=pub_host,
        )
        return

    # New container — allocate a fresh port.
    port = assign_web_port(project.id, task_id)
    meta["web_port"] = port

    env, volumes = build_task_env_and_volumes(project, task_id)

    agent_config_dir = _prepare_agent_config(project, project_id, task_id, agents, preset)
    volumes.append(VolumeSpec(agent_config_dir, CONTAINER_TEROK_CONFIG, sharing=Sharing.PRIVATE))

    token = _ensure_toad_token(agent_config_dir)
    meta["web_token"] = token

    env["TOAD_PUBLIC_PORT"] = str(_TOAD_PUBLIC_PORT)
    env["TOAD_INTERNAL_PORT"] = str(_TOAD_INTERNAL_PORT)

    # Resolve unrestricted mode: CLI flag → config → default (True)
    if unrestricted is None:
        _effective = resolve_agent_config(
            project_id,
            agent_config=project.agent_config,
            project_root=project.root,
            preset=preset,
        )
        _cfg_val = resolve_provider_value(
            "unrestricted", _effective, project.default_agent or "claude"
        )
        unrestricted = _cfg_val is None or _str_to_bool(_cfg_val)
    if unrestricted:
        _apply_unrestricted_env(env)

    meta["mode"] = "toad"
    meta["unrestricted"] = unrestricted
    if preset:
        meta["preset"] = preset
    write_task_meta(meta_path, meta)

    # Preserve the address family when the public host is a loopback — binding
    # ::1 to 127.0.0.1 would make the URL we print (``http://[::1]:…``)
    # unreachable.  LAN exposure still goes to ``0.0.0.0``.
    if pub_host == "::1":
        bind_addr = "[::1]"
    elif pub_host in _LOOPBACK_HOSTS:
        bind_addr = _LOCALHOST
    else:
        bind_addr = "0.0.0.0"  # nosec B104

    task_dir = project.tasks_root / str(task_id)
    # ``terok-toad-entry`` (from the caddy/toad roster entries) owns the
    # in-container choreography: it starts Caddy on ``_TOAD_PUBLIC_PORT``,
    # launches toad on loopback ``_TOAD_INTERNAL_PORT``, waits for both to
    # bind, and emits the ``TEROK_READY`` readiness marker.
    toad_cmd = f"terok-toad-entry --public-url http://{url_host(pub_host)}:{port} /workspace"
    run_hook(
        "pre_start",
        project.hook_pre_start,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=port,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        task_id=task_id,
        task_dir=task_dir,
        extra_args=["-p", f"{bind_addr}:{port}:{_TOAD_PUBLIC_PORT}"],
        command=["bash", "-lc", toad_cmd],
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=False)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=port,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    def _toad_ready(line: str) -> bool:
        """Return True when the supervisor wrapper reports both listeners are up."""
        return "TEROK_READY" in line

    runtime = _rt.resolve_runtime(project)
    ready = runtime.container(cname).stream_initial_logs(
        ready_check=_toad_ready,
        timeout_sec=None,
    )

    if not ready or not runtime.container(cname).running:
        print(f"Toad failed to start. Check logs: podman logs {cname}")
        raise SystemExit(1)

    run_hook(
        "post_ready",
        project.hook_post_ready,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=port,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    meta["ready_at"] = datetime.now(UTC).isoformat()
    write_task_meta(meta_path, meta)

    color_enabled = _supports_color()
    url = _toad_browser_url(pub_host, port, token)
    print(
        f"\n>> Toad is serving."
        f"\n- Name: {_green(cname, color_enabled)}"
        f"\n- URL:  {_hyperlink(_blue(url, color_enabled), url, enabled=color_enabled)}"
        f"\n- Logs: {_yellow(f'podman logs -f {cname}', color_enabled)}"
        f"\n- Stop: {_red(f'podman stop {cname}', color_enabled)}"
    )


__all__ = [
    "task_run_toad",
]
