# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Podman container launch + lifecycle primitives shared by every task runner.

``_run_container`` is the single launch path; ``_podman_start`` /
``_assert_running`` handle resuming a stopped container; ``_agent_runner``
and ``_project_runtime_flags`` assemble the podman invocation.
``_print_login_instructions`` is the shared "how to get a shell" footer.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from terok.lib.core.config import is_experimental
from terok.lib.integrations.executor import (
    AgentRunner,
    BuildError,
    ensure_krun_host_keypair,
)
from terok.lib.integrations.sandbox import (
    DEFAULT_GUEST_SSHD_PORT,
    DEFAULT_SSH_HOST,
    LifecycleHooks,
    PodmanRuntime,
    Sandbox,
    VolumeSpec,
)

from ...core import runtime as _rt
from ...core.config import make_sandbox_config
from ...core.projects import load_project
from ...core.task_state import has_gpu
from ...util.ansi import blue as _blue, yellow as _yellow
from ..tasks import dossier_path, tasks_meta_dir

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig


def _podman_start(project: ProjectConfig, cname: str) -> None:
    """Start an existing container, raising SystemExit on failure."""
    try:
        _rt.resolve_runtime(project).container(cname).start()
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except RuntimeError as exc:
        raise SystemExit(f"Failed to start container:\n{exc}")


def _assert_running(project: ProjectConfig, cname: str) -> None:
    """Verify a container is running after start, or raise SystemExit."""
    post_state = _rt.resolve_runtime(project).container(cname).state
    if post_state != "running":
        raise SystemExit(
            f"Container {cname} failed to start (state: {post_state}). "
            f"Check logs with: podman logs {cname}"
        )


def _print_login_instructions(project_id: str, task_id: str, cname: str, color: bool) -> None:
    """Print how to log into a CLI container; warn if the vault recovery key is unconfirmed.

    Resolves the runtime per *project_id* so the printed raw-command
    line matches what the operator's about to invoke — under crun
    that's ``podman exec``; under krun it's
    ``ssh -p <host_port> -i <key> … dev@127.0.0.1``.
    """
    project = load_project(project_id)
    login_cmd = f"terok login {project_id} {task_id}"
    raw_cmd = shlex.join(
        _rt.resolve_runtime(project).container(cname).login_command(command=("bash",))
    )
    print(f"Login with: {_blue(login_cmd, color)}")
    print(f"  (or:      {_blue(raw_cmd, color)})")
    _maybe_warn_recovery_unconfirmed(color)


def _maybe_warn_recovery_unconfirmed(color: bool) -> None:
    """One-line nudge after every CLI task launch when no recovery ack is on disk.

    Cheap probe: bundled marker+source lookup via
    [`recovery_status`][terok_sandbox.recovery_status].  Failures
    (missing wheel symbol on an old sandbox pin, transient I/O) are
    swallowed so the launch-time message never blocks the operator
    from getting their login command.

    Escalates from yellow ``warn`` to red ``error`` text when the
    resolver landed on the session-unlock tmpfs tier and the marker
    is missing — the passphrase is wiped on the next reboot and the
    vault becomes unrecoverable then.
    """
    try:
        from terok.lib.integrations.sandbox import recovery_status
    except ImportError:
        # Older sandbox pin without the wrapper — the warning is
        # opt-in by adapter exposure; absence is fine.
        return
    try:
        status = recovery_status()
    except Exception:  # noqa: BLE001 — best-effort hint, never the source of truth
        return
    if status.acknowledged:
        return
    if status.urgent:
        from ...util.ansi import red as _red

        print(
            _red(
                "Vault recovery key UNCONFIRMED and the passphrase lives ONLY"
                " in the session-unlock tmpfs file — it WILL be wiped on the"
                " next reboot and your vault becomes UNRECOVERABLE then.\n"
                "  Save it NOW off-host: terok vault passphrase reveal",
                color,
            )
        )
        return
    msg = (
        "Vault recovery key unconfirmed — every keystore tier is"
        " machine-bound, so a hardware failure strands the vault.\n"
        "  Save it off-host: terok vault passphrase reveal"
    )
    print(_yellow(msg, color))


def _run_container(
    *,
    cname: str,
    image: str,
    env: dict[str, str],
    volumes: list[VolumeSpec],
    project: ProjectConfig,
    task_id: str,
    task_dir: Path,
    extra_args: list[str] | None = None,
    command: list[str] | None = None,
    hooks: LifecycleHooks | None = None,
) -> None:
    """Launch a detached task container, annotated for clearance enrichment.

    A single ``dossier.meta_path`` OCI annotation binds the container
    to its task identity: it points at the wire-dossier JSON file
    terok writes per task (``{project, task, name}`` in wire shape).
    Shield rereads that file on every event emit, so a task rename
    mid-run surfaces the fresh label in the next popup without touching
    the annotation.  Project/task IDs lived as separate annotations in
    earlier iterations; the JSON file made them redundant.

    Podman command assembly (userns, shield/bypass, GPU, env redaction,
    CDI detection) is delegated to `AgentRunner.launch_prepared`.
    In sealed isolation mode (``project.is_sealed``) the sandbox splits
    into create → copy → start instead of a single ``podman run -d``.

    Args:
        cname: Container name (``--name``).
        image: Container image to run.
        env: Environment variables to pass via ``-e``.
        volumes: Typed volume specs (sandbox decides mount vs inject).
        project: The resolved [`ProjectConfig`][terok.cli.commands.sickbay.ProjectConfig] (used for GPU flag).
        task_id: Task identifier — the second component of the clearance
            annotation triple.
        task_dir: Per-task directory (used for per-task shield state).
        extra_args: Additional ``podman run`` flags inserted after the GPU
            args (e.g. ``["-p", "127.0.0.1:8080:7860"]``).
        command: Optional command + args appended after the image name.
        hooks: Optional lifecycle callbacks fired around the launch.
    """
    # OCI annotation under the ``dossier.*`` namespace flows through to
    # the shield reader, which picks it up at hook spawn time and uses
    # the pointed-at JSON file as ``ClearanceEvent.dossier`` on every
    # event.  The JSON file IS the wire dossier — wire-shape keys, no
    # projection, no snapshot — so one annotation is enough.
    task_dossier_path = dossier_path(tasks_meta_dir(project.id), task_id)
    annotations = [
        "--annotation",
        f"dossier.meta_path={task_dossier_path}",
    ]
    merged_args = (
        annotations + list(extra_args or ()) + _project_runtime_flags(project, cname=cname)
    )
    try:
        _agent_runner(project).launch_prepared(
            env=env,
            volumes=volumes,
            image=image,
            command=list(command or ()),
            name=cname,
            task_dir=task_dir,
            gpu=has_gpu(project),
            memory=project.memory_limit,
            cpus=project.cpu_limit,
            unrestricted="TEROK_UNRESTRICTED" in env,
            sealed=project.is_sealed,
            hooks=hooks,
            extra_args=merged_args,
            hostname=cname,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"podman not found; please install podman ({exc})") from exc
    except BuildError as exc:
        raise SystemExit(str(exc)) from exc


def _agent_runner(project: ProjectConfig) -> AgentRunner:
    """Return an `AgentRunner` whose `Sandbox` is bound to *project*'s runtime.

    Resolving the runtime here (rather than letting `Sandbox` default
    to crun) is what makes ``run.runtime: krun`` actually take effect
    on launch — `AgentRunner` then routes every podman invocation
    through the matching backend.
    """
    cfg = make_sandbox_config()
    runtime = _rt.resolve_runtime(project)
    return AgentRunner(sandbox=Sandbox(cfg, runtime=runtime))


def _project_runtime_flags(project: ProjectConfig, *, cname: str) -> list[str]:
    """Return extra ``podman run`` flags derived from project-level capabilities.

    ``run.nested_containers`` → ``--security-opt label=nested`` plus
    ``--device /dev/fuse``.  ``label=nested`` confines the outer container
    to the SELinux type that permits nested container operations
    (devpts mount, rootless overlay setup) without disabling labelling;
    ``/dev/fuse`` is required by rootless podman's fuse-overlayfs driver.
    Available on podman v4.5.0+ (April 2023); older podmans error with
    "unknown label option: nested" and the user is expected to upgrade.

    ``run.runtime: krun`` → ``--runtime krun`` plus krun OCI annotations
    for microVM sizing, a per-container ``-p 127.0.0.1:<host>:22`` port
    forward bridging into the guest's sshd via podman's passt, **and** a
    bind-mount of the live host SSH public key into the guest's
    ``/etc/ssh/authorized_keys.d/terok``.  The L0 image ships an empty
    placeholder there; the mount overlays it so the guest's sshd
    accepts our private key.  Validates the krun-incompatible
    combinations before emitting any flag so the operator sees a clear
    error rather than a podman launch failure.
    """
    del cname  # signature kept for caller stability; no longer read here
    flags: list[str] = []
    if project.nested_containers:
        flags += ["--security-opt", "label=nested", "--device", "/dev/fuse"]
    if project.runtime == "krun":
        _validate_krun_compatibility(project)
        flags += ["--runtime", "krun"]
        if project.krun_cpus is not None:
            flags += ["--annotation", f"run.oci.krun.cpus={project.krun_cpus}"]
        if project.krun_ram_mib is not None:
            flags += ["--annotation", f"run.oci.krun.ram_mib={project.krun_ram_mib}"]
        # Reserve a free loopback TCP port and forward it to the guest's
        # sshd.  ``reserve_port`` binds-then-releases, so there's a
        # small race window before podman picks the port back up — same
        # pattern the executor uses for toad's web port; tolerated as a
        # rare-by-construction failure that fails loud rather than
        # silently mis-binding.  The bind is pinned to ``127.0.0.1`` so
        # the forward isn't reachable from external interfaces — pasta's
        # default binds ``*``, which would otherwise expose the krun
        # task's sshd to any host on the network.  No annotation: podman
        # already records the mapping, and ``podman_port_resolver`` on
        # the host side reads it back via ``podman port`` at exec time.
        with PodmanRuntime().reserve_port() as reservation:
            host_port = reservation.port
        flags += [
            "-p",
            f"{DEFAULT_SSH_HOST}:{host_port}:{DEFAULT_GUEST_SSHD_PORT}",
        ]
        # Bind-mount the live host pubkey into the guest.  The L0 image
        # ships an empty placeholder at this path; the mount overlays
        # it so the guest's sshd accepts our identity.  This is what
        # keeps the L0 image byte-identical for crun and krun consumers
        # — no per-installation secret baked in at build.
        keypair = ensure_krun_host_keypair(cfg=make_sandbox_config())
        flags += [
            "-v",
            f"{keypair.public_path}:/etc/ssh/authorized_keys.d/terok:ro,z",
        ]
        # Explicit runtime signal for the in-container init script's
        # sshd launch.  init-ssh-and-repo.sh gates the sshd supervisor
        # loop on this value (``== "krun"``) — explicit signal rather
        # than inferring from the bind-mount, so a botched L0 with a
        # non-empty authorized_keys placeholder can't accidentally
        # expose sshd under crun.
        flags += ["-e", "TEROK_CONTAINER_RUNTIME=krun"]
        # Override the L0's ``USER dev`` directive — the in-guest sshd
        # needs to start as root so it can listen on TCP 22, write to
        # /run/sshd, etc., and drop to the authenticated user
        # (``dev`` or ``root``) on connection.  ``USER dev`` is the
        # right default under crun for AI agents that refuse uid 0;
        # under krun the sshd-via-podman model takes over and the
        # agent's session uid comes from which ``ssh user@…`` the
        # operator picks, not from the container's PID 1.
        flags += ["--user", "root"]
    return flags


def _validate_krun_compatibility(project: ProjectConfig) -> None:
    """Reject combinations that can't be honoured under krun.

    Both the env-var path
    ([`resolve_runtime`][terok.lib.core.runtime.resolve_runtime]) and
    the project-config path (this function) must gate on the global
    ``experimental`` flag — otherwise a typo or accidental config edit
    silently switches the workload to the less-audited experimental
    backend.

    - ``experimental``: required for krun selection by any path.
    - ``nested_containers``: krun guests can't host nested podman/docker
      with our current image; ask the operator to drop one of the two.
    """
    if not is_experimental():
        raise SystemExit(
            "run.runtime: krun requires the experimental flag.  Set "
            "`experimental: true` in your config.yml, or pass "
            "`--experimental` on the command line."
        )

    if project.nested_containers:
        raise SystemExit(
            "run.runtime: krun is incompatible with run.nested_containers: true — "
            "the krun guest's hardened sshd doesn't host a nested-container stack.  "
            "Pick one or move the nested workload to a crun task."
        )


__all__ = [
    "_agent_runner",
    "_assert_running",
    "_podman_start",
    "_print_login_instructions",
    "_project_runtime_flags",
    "_run_container",
]
