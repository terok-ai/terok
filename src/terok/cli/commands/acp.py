# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task ACP endpoint commands: ``terok acp list`` / ``terok acp connect``.

The ``acp`` group is the user-facing surface for the per-task ACP
proxy: each running task gets a Unix socket that aggregates the
container's in-image agents (claude, codex, …) behind ACP's standard
model selector as namespaced ``agent:model`` ids.

This module owns only the orchestration glue terok adds on top of
``terok-executor``'s host-proxy daemon:

- ``acp list`` walks projects → tasks → endpoints,
- ``acp connect`` translates ``(project_id, task_id)`` to a container
  name and per-task socket path, lazy-spawns the executor daemon
  (``python -m terok_executor.acp.daemon``), and bridges the caller's
  stdio to that socket so an ACP client (Zed, Toad, …) launching us
  as its agent server speaks JSON-RPC straight through.

Errors raised before the bridge is up are surfaced both on stderr
*and* as a single JSON-RPC error frame on stdout: most ACP clients
launch the agent as a subprocess and never display its stderr, so
the error frame is what makes "ACP daemon could not start" visible
in the client UI.
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import subprocess  # nosec B404 — only used with explicit argv (no shell, no untrusted input)
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from terok_executor.acp.daemon import acp_socket_is_live

from ...lib.core.config import is_experimental
from ...lib.core.paths import acp_log_path, acp_socket_path
from ...lib.domain.facade import list_projects
from ._completers import add_project_id, add_task_id

if TYPE_CHECKING:
    from terok_executor import ACPEndpointStatus

    from ...lib.domain.project import Project


_DAEMON_BIND_TIMEOUT_SEC = 6.0
"""Generous bind-timeout ceiling for the freshly spawned daemon, so a
slow startup doesn't show up as a phantom failure."""


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``acp`` subcommand group with ``list`` / ``connect``."""
    p = subparsers.add_parser(
        "acp",
        help="Per-task ACP (Agent Client Protocol) endpoint management",
    )
    sub = p.add_subparsers(dest="acp_cmd", required=True)

    p_list = sub.add_parser("list", help="List ACP endpoints across running tasks")
    add_project_id(p_list, nargs="?", default=None)

    p_connect = sub.add_parser(
        "connect",
        help="Connect stdio to a task's ACP socket (spawning the daemon if needed)",
    )
    add_project_id(p_connect)
    add_task_id(p_connect)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``acp`` commands; return ``True`` when consumed."""
    if args.cmd != "acp":
        return False
    if args.acp_cmd == "list":
        _cmd_list(getattr(args, "project_id", None))
    elif args.acp_cmd == "connect":
        _cmd_connect(args.project_id, args.task_id)
    return True


# ── list ─────────────────────────────────────────────────────────────────


def _cmd_list(project_id_filter: str | None) -> None:
    """Print one row per ACP endpoint, grouped by project."""
    projects = _projects_to_show(project_id_filter)
    # ``from __future__ import annotations`` (top of module) makes the
    # ACPEndpointStatus reference below a deferred string — no runtime
    # import needed, so the executor stays out of the cold-start path.
    rows: list[tuple[str, str, ACPEndpointStatus, str | None, Path]] = []
    for project in projects:
        for ep in project.acp_endpoints():
            rows.append((ep.project_id, ep.task_id, ep.status, ep.bound_agent, ep.socket_path))

    if not rows:
        print("No ACP endpoints found (no running tasks).")
        return

    # Render: project / task / status / bound-agent / path.  Status drives
    # the colour-free hint at the end of each row so the user knows what
    # they can do.
    width_pid = max(len("PROJECT"), *(len(p) for p, _t, _s, _b, _ in rows))
    width_tid = max(len("TASK"), *(len(t) for _p, t, _s, _b, _ in rows))
    width_sta = max(len("STATUS"), *(len(s.value) for _p, _t, s, _b, _ in rows))
    print(f"{'PROJECT':<{width_pid}}  {'TASK':<{width_tid}}  {'STATUS':<{width_sta}}  AGENT  PATH")
    for pid, tid, status, bound, path in rows:
        bound_disp = bound or "-"
        print(
            f"{pid:<{width_pid}}  {tid:<{width_tid}}  "
            f"{status.value:<{width_sta}}  {bound_disp:<6} {path}"
        )


def _projects_to_show(project_id_filter: str | None) -> list[Project]:
    """Resolve project filter to the list of project objects to walk."""
    from ...lib.domain.facade import get_project

    if project_id_filter:
        return [get_project(project_id_filter)]
    project_infos = list_projects()
    return [get_project(info.id) for info in project_infos]


# ── connect ──────────────────────────────────────────────────────────────


def _check_experimental_ack() -> None:
    """Refuse to run ``acp connect`` unless experimental features are enabled.

    Why experimental: by default the IDE has no view into the agent's
    in-container filesystem, so a connecting IDE sees *zero* of the
    agent's edits.  The only working live-view today comes from the
    user deliberately pointing their IDE at the per-task
    ``workspace-dangerous`` folder; that folder's lifecycle and risk
    profile are documented in the general terok design — not an
    ACP-specific concern.  Until ACP ``fs/*`` callbacks land or a
    sandbox-friendly shared view ships, the protocol's usefulness
    here is limited.

    Gates on the codebase's standard experimental axis
    (:func:`is_experimental` — ``--experimental`` flag or
    ``experimental: true`` in ``config.yml``); same axis as
    claude-OAuth-proxying, codex-vaulted-OAuth, and friends.
    """
    if is_experimental():
        return
    sys.stderr.write(
        "terok acp: experimental.  By default the IDE has no view into the\n"
        "agent's filesystem, so functionality is limited.  Pointing the IDE\n"
        "at the task's workspace-dangerous folder (see terok docs for the\n"
        "design rationale) gives a working live-view; ACP fs/* callbacks\n"
        "and a sandbox-friendly shared view are on the roadmap.\n"
        "\n"
        "Pass --experimental, or set experimental: true in config.yml,\n"
        "to proceed.\n"
    )
    raise SystemExit(2)


def _cmd_connect(project_id: str, task_id: str) -> None:
    """Bridge the caller's stdio to a task's ACP socket.

    Refuses to run unless :func:`_check_experimental_ack` passes.
    Otherwise spawns the executor's per-container ACP daemon if the
    socket is not already live, waits for it to bind, then runs the
    in-process pump (stdin → AF_UNIX socket, socket → stdout) until
    either side reaches EOF.
    """
    _check_experimental_ack()
    sock_path = acp_socket_path(project_id, task_id)
    log_path = acp_log_path(project_id, task_id)
    if not acp_socket_is_live(sock_path):
        daemon = _spawn_daemon(project_id, task_id, sock_path, log_path)
        _wait_for_socket(
            sock_path, timeout=_DAEMON_BIND_TIMEOUT_SEC, daemon=daemon, log_path=log_path
        )
    _inprocess_pump(sock_path, log_path=log_path)


def _spawn_daemon(
    project_id: str, task_id: str, sock_path: Path, log_path: Path
) -> subprocess.Popen:
    """Resolve project+task → container name and spawn the executor daemon.

    The daemon module is in ``terok-executor`` — invoked via
    ``sys.executable -m`` so the spawn doesn't depend on
    ``terok-executor`` being on ``PATH`` (it isn't, after a plain
    ``pipx install terok``).  Daemon stderr is appended to *log_path*
    so its tracebacks outlive the detached spawn — the bridge can
    point the user at this file when the connection RSTs mid-session.
    Returns the ``Popen`` so the bind-wait loop can fail fast on
    early exit.
    """
    from ...lib.orchestration.tasks import (
        container_name as resolve_container_name,
        get_task_meta,
    )

    task_meta = get_task_meta(project_id, task_id)
    cname = resolve_container_name(project_id, task_meta.mode, task_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_path, "ab", buffering=0)  # noqa: SIM115 — handed to Popen
    try:
        return subprocess.Popen(  # nosec B603 — argv = [interpreter, -m, module, container, sock]
            [sys.executable, "-m", "terok_executor.acp.daemon", cname, str(sock_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        # Popen never inherited the fd — close it ourselves before
        # the exception propagates so we don't leak it on the host.
        log_fd.close()
        raise


def _wait_for_socket(
    path: Path,
    *,
    timeout: float,
    daemon: subprocess.Popen,
    log_path: Path,
) -> None:
    """Block until *daemon* binds *path* or *timeout* elapses.

    Polls *daemon* alongside the bind probe so a startup crash surfaces
    immediately with the daemon's exit code instead of stalling the
    full *timeout* and reporting a misleading "did not bind" error.
    Both failure paths exit via :func:`_fail` so the message reaches
    stderr (and any ACP client that captures the agent subprocess's
    stderr, like Zed at WARN level), naming *log_path* so the user
    can read the daemon's traceback.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon.poll() is not None:
            _fail(
                f"ACP daemon exited before binding {path} "
                f"(exit code {daemon.returncode}); see {log_path}"
            )
        if acp_socket_is_live(path):
            return
        time.sleep(0.05)
    _fail(f"ACP daemon did not bind {path} within {timeout:.1f}s; see {log_path}")


def _fail(message: str) -> None:
    """Print *message* on stderr and exit with code 1.

    Earlier this also wrote a JSON-RPC ``{id: null, error: …}`` frame
    on stdout so a launching client (Zed) would surface the cause in
    its UI.  But that frame is malformed under JSON-RPC 2.0 — Zed's
    parser logs ``received message with neither id nor method`` and
    drops it.  Stderr is the right channel for unsolicited errors
    here: ACP clients that capture the agent subprocess's stderr
    (Zed does, at WARN level) get the message verbatim, and a real
    terminal user sees it directly.
    """
    print(f"terok: {message}", file=sys.stderr)
    raise SystemExit(1)


# ── stdio ↔ socket bridge ─────────────────────────────────────────────────


def _inprocess_pump(sock_path: Path, *, log_path: Path) -> None:
    """Bridge stdin/stdout to *sock_path* until either side reaches EOF.

    Connects to the daemon's listener, then multiplexes:
    stdin EOF triggers ``shutdown(SHUT_WR)`` so the daemon can drain
    its final reply; daemon EOF returns from the loop and the
    ``finally`` block closes the socket.  Non-blocking IO with
    :func:`select` for multiplexing; partial sends/writes are looped
    until drained.

    A ``ConnectionResetError`` from ``recv`` means the daemon's
    process ended abruptly mid-session — the kernel sends RST when
    there are unread bytes the daemon will never process.  That gets
    surfaced via :func:`_fail` so the ACP client sees a JSON-RPC
    error pointing at *log_path* (where the daemon's traceback lives)
    rather than a bare Python stack from the bridge.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(sock_path))
    sock.setblocking(False)
    stdin_fd = sys.stdin.buffer.fileno()
    stdout_fd = sys.stdout.buffer.fileno()
    stdin_open = True
    try:
        while True:
            # Drop stdin from the watch set after EOF — otherwise
            # ``select`` keeps marking it ready and we'd attempt a
            # second ``shutdown(SHUT_WR)``, dropping the daemon's
            # final reply on the floor.
            read_fds: list[object] = [sock]
            if stdin_open:
                read_fds.append(stdin_fd)
            ready, _, _ = select.select(read_fds, [], [])
            if stdin_open and stdin_fd in ready:
                stdin_open = _forward_stdin_to_socket(stdin_fd, sock)
            if sock in ready and not _forward_socket_to_stdout(sock, stdout_fd, log_path):
                return
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _forward_stdin_to_socket(stdin_fd: int, sock: socket.socket) -> bool:
    """Forward one stdin chunk to *sock*; return ``False`` once stdin hits EOF."""
    data = os.read(stdin_fd, 4096)
    if not data:
        # ``shutdown`` may raise if the daemon already closed its end —
        # tolerate that, the SHUT_WR is advisory anyway.
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        return False
    _send_all(sock, data)
    return True


def _send_all(sock: socket.socket, data: bytes) -> None:
    """Drain *data* into the non-blocking *sock*, looping past short sends.

    A non-blocking ``send`` may write fewer bytes than requested under
    backpressure, and ``BlockingIOError`` signals "kernel send buffer
    full" — wait for write-readiness via a single-fd ``select`` and
    retry until the whole frame is committed.
    """
    view = memoryview(data)
    while view:
        try:
            sent = sock.send(view)
            view = view[sent:]
        except BlockingIOError:
            select.select([], [sock], [])


def _forward_socket_to_stdout(sock: socket.socket, stdout_fd: int, log_path: Path) -> bool:
    """Forward one socket chunk to *stdout_fd*; return ``False`` on daemon EOF.

    A ``ConnectionResetError`` from ``recv`` means the daemon process
    died with bytes still in its receive buffer (kernel sends RST,
    not FIN).  Surface it as a JSON-RPC error frame pointing at
    *log_path* — never escapes as a bare Python traceback.
    """
    try:
        data = sock.recv(4096)
    except BlockingIOError:
        return True
    except ConnectionResetError:
        _fail(f"ACP daemon connection reset (process exited mid-session); see {log_path}")
    if not data:
        return False
    # ``os.write`` may also write fewer bytes than supplied (rare on
    # regular fds, but possible on pipes/ptys).
    view = memoryview(data)
    while view:
        written = os.write(stdout_fd, view)
        view = view[written:]
    return True
