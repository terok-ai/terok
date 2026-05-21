# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Podman container launch + lifecycle primitives shared by every task runner.

``_run_container`` is the single launch path; ``_podman_start`` /
``_assert_running`` handle resuming a stopped container; ``_agent_runner``
and ``_project_runtime_flags`` assemble the podman invocation.
``_print_login_instructions`` is the shared "how to get a shell" footer.
"""

from __future__ import annotations

import dataclasses
import shlex
from typing import TYPE_CHECKING

from terok.lib.core.config import is_experimental
from terok.lib.integrations.executor import (
    AgentRunner,
    BuildError,
    krun_launch_args,
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
    annotations = {"dossier.meta_path": str(task_dossier_path)}
    merged_args = list(extra_args or ()) + _project_runtime_flags(project, cname=cname)
    if project.runtime == "krun":
        hooks = _chain_krun_dns_rewrite(hooks, task_dir)
    try:
        _agent_runner(project).launch_prepared(
            env=env,
            volumes=volumes,
            image=image,
            command=list(command or ()),
            name=cname,
            task_dir=task_dir,
            gpu=has_gpu(project),
            memory=project.memory,
            cpus=project.cpus,
            unrestricted="TEROK_UNRESTRICTED" in env,
            sealed=project.is_sealed,
            hooks=hooks,
            extra_args=merged_args,
            hostname=cname,
            annotations=annotations,
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

    ``run.runtime: krun`` → ``--runtime krun``, a per-container
    ``-p 127.0.0.1:<host>:22`` port forward bridging into the guest's
    sshd via podman's passt, **and** a bind-mount of the live host SSH
    public key into the guest's ``/etc/ssh/authorized_keys.d/terok``.
    The L0 image ships an empty placeholder there; the mount overlays
    it so the guest's sshd accepts our private key.  Validates the
    krun-incompatible combinations before emitting any flag so the
    operator sees a clear error rather than a podman launch failure.

    Resource sizing under krun reuses the standard ``run.memory`` /
    ``run.cpus`` knobs — podman translates ``--memory`` / ``--cpus``
    through to the OCI spec krun reads to size the microVM, so no
    separate annotation pathway is needed.
    """
    del cname  # signature kept for caller stability; no longer read here
    flags: list[str] = []
    if project.nested_containers:
        flags += ["--security-opt", "label=nested", "--device", "/dev/fuse"]
    if project.runtime == "krun":
        _validate_krun_compatibility(project)
        flags += ["--runtime", "krun"]
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
        flags += krun_launch_args(cfg=make_sandbox_config())
    return flags


def _chain_krun_dns_rewrite(hooks: LifecycleHooks | None, task_dir: Path) -> LifecycleHooks:
    """WORKAROUND(krun-shield-dns): point shield's bind-mounted resolv.conf
    at pasta's forwarder instead of the in-netns dnsmasq.

    Shield writes ``nameserver 127.0.0.1`` at ``<task>/shield/resolv.conf``
    and bind-mounts it over ``/etc/resolv.conf`` so the in-netns dnsmasq
    can intercept DNS for clearance + audit.  Under krun the guest's
    ``127.0.0.1`` is the microVM's own loopback (TSI doesn't redirect
    127.0.0.0/8), so queries land nowhere and name resolution dies.

    Rewrite the file to ``nameserver 169.254.1.1`` — pasta's link-local
    forwarder, the same address shield's nft already permits ``:53`` to.
    TSI surfaces the guest's connect to a host-side socket inside the
    netns, pasta answers, the guest resolves.  Cost: dnsmasq sits idle,
    so the clearance flow no longer fires on hostnames (IP-based
    clearance still works — egress filtering on TCP/UDP is unaffected
    because TSI surfaces traffic where shield's nft hooks live).

    Fires from ``LifecycleHooks.pre_start``, which runs after shield's
    own ``pre_start`` wrote the file and before podman exec — the only
    window where the file is guaranteed to exist and the container has
    not yet read it.

    Proper fix: shield-side krun awareness (have shield write the right
    address itself, conditional on the target runtime).  Remove this
    helper when shield grows that.
    """
    shield_resolv = task_dir / "shield" / "resolv.conf"
    prior = hooks.pre_start if hooks else None

    def _patch() -> None:
        if prior is not None:
            prior()
        if shield_resolv.is_file():
            shield_resolv.write_text("nameserver 169.254.1.1\noptions ndots:0\n")

    base = hooks or LifecycleHooks()
    return dataclasses.replace(base, pre_start=_patch)


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
