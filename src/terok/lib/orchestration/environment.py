# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Container environment and volume assembly for task containers.

Translates project configuration and security mode into the environment
variables and volume mounts that ``podman run`` needs when launching a
task container.  Shared config mounts and base env vars are delegated to
[`terok_executor.assemble_container_env`][terok_executor.assemble_container_env]; this module adds terok-specific
concerns (gate server, vault with OAuth/socket/SSH support).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from terok.lib.integrations.sandbox import (
    SandboxConfig,
    VolumeSpec,
    mint_gate_token,
)

from ..core.config import (
    exposed_credential_providers,
    make_sandbox_config,
    sandbox_live_mounts_dir,
)
from ..core.projects import ProjectConfig
from ..util.host_cmd import WORKSPACE_DANGEROUS_DIRNAME

if TYPE_CHECKING:
    # Type-only import: terok_executor doesn't re-export AgentRoster at the
    # top level, but the runtime convention only applies to actual imports.
    from terok.lib.integrations.executor import AgentRoster

_logger = logging.getLogger(__name__)

_CONTAINER_RUNTIME_DIR = "/run/terok"
"""Container-side mount point — must match [`terok_sandbox.CONTAINER_RUNTIME_DIR`][terok_sandbox.CONTAINER_RUNTIME_DIR]."""

_CONTAINER_GATE_PORT = 9418
"""Fixed in-container port the container reaches the gate on, both modes.

Must match the hardcoded ``TCP-LISTEN:9418`` in ``ensure-bridges.sh`` — that
bridge forwards to the per-container ``gate-server.sock`` (socket mode) or the
host TCP port (TCP mode), so the CODE_REPO / CLONE_FROM URL the container sees
is always ``http://localhost:9418/<repo>``.
"""


def project_mounts_dir(project: ProjectConfig) -> Path:
    """Return the effective agent-config mount tree for *project*.

    ``credentials_scope == "project"`` carves out a private subtree under
    the project's root; the default ``"shared"`` returns the host-wide
    [`sandbox_live_mounts_dir`][terok.lib.core.config.sandbox_live_mounts_dir]
    every project sees.  Pure orchestration helper so the value object
    stays free of the global config dependency.
    """
    if project.credentials_scope == "project":
        return project.project_mounts_dir
    return sandbox_live_mounts_dir()


def _check_project_credentials_present(project: ProjectConfig) -> None:
    """Fail fast when a project-scope project has an empty vault row.

    The shared-scope flow tolerates a missing credential — the agent
    prompts for login on first turn and the user might prefer that.  But
    a project that opted into ``credentials_scope: project`` has just
    explicitly *isolated* its bucket: silent fallback would defeat the
    isolation, and an empty bucket is almost always a user oversight
    (they edited project.yml but forgot ``terok auth --project <id>``).
    Surface it before the container starts so the error message names
    the recovery command instead of bubbling up from inside the agent.
    """
    if project.credentials_scope != "project":
        return

    from terok.lib.integrations.executor import list_authenticated_agents

    if list_authenticated_agents(scope=project.credential_set):
        return

    agent = project.default_agent or "claude"
    raise SystemExit(
        f"Project '{project.id}' is configured for per-project credentials "
        f"(credentials.scope: project) but its vault bucket is empty.\n"
        f"Authenticate before launching tasks:\n\n"
        f"  terok auth {agent} --project {project.id}\n"
    )


_SPEC_CONSUMED_SEC_ENV_KEYS = frozenset({"CODE_REPO", "CLONE_FROM", "GIT_BRANCH"})
"""``sec_env`` keys already routed via ``ContainerEnvSpec`` (see
[`build_task_env_and_volumes`][terok.lib.orchestration.environment.build_task_env_and_volumes]
~line 458).  Everything else from ``sec_env`` is forwarded to the container
verbatim — keep this set in sync with the spec field assignments, not with
each new gate env var added downstream."""


def _gate_url(gate_repo: Path, gate_base: Path, port: int, token: str) -> str:
    """Build the ``http://`` URL for a gate repo served by the per-container gate.

    The token is embedded as the Basic Auth username in the URL so that git
    handles authentication natively.  Uses the repo directory name as the URL
    path — the gate serves repos as direct children of its base path.

    The container always reaches the gate via the localhost socat bridge
    started by ``ensure-bridges.sh`` (``TCP-LISTEN:9418`` → per-container
    socket in socket mode, or → host TCP port in TCP mode), so the URL points
    to ``localhost`` in both modes.

    Raises ``SystemExit`` if the repo is not a direct child of the gate base,
    since the gate cannot serve repos from arbitrary locations.
    """
    if gate_repo.resolve().parent != gate_base.resolve():
        raise SystemExit(
            "Configured gate.path is not servable by the gate.\n"
            f"  Gate repo: {gate_repo}\n"
            f"  Gate base: {gate_base}\n"
            "Move the repo under the gate base directory, or adjust\n"
            "gate_server.repos_dir / paths.root in global config."
        )
    return f"http://{token}@localhost:{port}/{gate_repo.name}"


def _resolve_gate_port() -> int:
    """Resolve the in-container port the gate is reached on.

    The gate runs inside the per-container supervisor; the container
    reaches it through the fixed in-container socat-bridge port
    (``TCP-LISTEN:9418`` in ``ensure-bridges.sh``) in both socket and TCP
    modes — the bridge forwards to the per-container unix socket or the
    host TCP port respectively.  Always the same in-container port.
    """
    return _CONTAINER_GATE_PORT


def _gatekeeping_repo_env(
    project: ProjectConfig,
    gate_base: Path,
    gate_port: int,
) -> dict[str, str]:
    """Repo-access env for a gatekeeping-class task — the gate *is* origin.

    The container clones and pushes through the gate, so a missing gate
    mirror is fatal.  Mints a fresh per-task gate token, embeds it in the
    gate URL, and exposes it via ``TEROK_GATE_TOKEN`` so the per-container
    supervisor can validate the requests it receives.
    """
    gate_repo = project.gate_path
    if not gate_repo.exists():
        raise SystemExit(
            f"Git gate missing for project '{project.id}'.\n"
            f"Expected at: {gate_repo}\n"
            f"Run 'terok project gate-sync {project.id}' to create/update the local mirror."
        )
    token = mint_gate_token()
    gate_url = _gate_url(gate_repo, gate_base, gate_port, token)
    env: dict[str, str] = {"CODE_REPO": gate_url, "TEROK_GATE_TOKEN": token}
    if project.default_branch:
        env["GIT_BRANCH"] = project.default_branch
    if project.expose_external_remote and project.upstream_url:
        env["EXTERNAL_REMOTE_URL"] = project.upstream_url
    return env


def _online_repo_env(
    project: ProjectConfig,
    gate_base: Path,
    gate_port: int,
) -> dict[str, str]:
    """Repo-access env for an online-class task — clones from upstream directly.

    The gate is used only as an opt-in clone accelerator
    (``gate.enabled``) when its mirror exists; ``gate.enabled: false``
    is the escape hatch for hosts that cannot use the gate.  When the
    gate is in play it mints a fresh per-task token, embeds it in the
    gate URL, and exposes it via ``TEROK_GATE_TOKEN`` for the
    per-container supervisor to validate.
    """
    env: dict[str, str] = {}
    gate_repo = project.gate_path
    if project.gate_enabled and gate_repo.exists():
        token = mint_gate_token()
        gate_url = _gate_url(gate_repo, gate_base, gate_port, token)
        env["CLONE_FROM"] = gate_url
        env["TEROK_GATE_TOKEN"] = token
        # Surface the gate as a named "gate" remote alongside origin
        # (which points at upstream).  The agent can push WIP branches
        # host-locally without going to upstream — a free checkpoint
        # that makes the gate's role coherent across both modes.  In
        # gatekeeping mode the gate is already origin, so this env var
        # is online-mode-only.  Consumed by init-ssh-and-repo.sh.
        env["GATE_REMOTE_URL"] = gate_url
    if project.upstream_url:
        env["CODE_REPO"] = project.upstream_url
        if project.default_branch:
            env["GIT_BRANCH"] = project.default_branch
    return env


def _security_mode_env_and_volumes(
    project: ProjectConfig,
    cfg: SandboxConfig,
    *,
    use_socket: bool = False,
) -> tuple[dict[str, str], list[VolumeSpec]]:
    """Return env vars and volumes for the project's security mode."""
    volumes: list[VolumeSpec] = []
    gate_repo = project.gate_path
    gate_base = cfg.gate_base_path
    gate_port = _resolve_gate_port()

    if project.security_class == "gatekeeping":
        env = _gatekeeping_repo_env(project, gate_base, gate_port)
    else:
        env = _online_repo_env(project, gate_base, gate_port)

    # Gate socket path for the container-side socat bridge.  The gate now
    # runs inside the per-container supervisor, which binds
    # ``gate-server.sock`` inside the per-container ``/run/terok`` dir — the
    # same dir the supervisor's vault/ssh sockets live in, mounted by the
    # executor's launch flow.  No host bind-mount is needed here; we only
    # tell the bridge the well-known in-container path it connects to.
    if use_socket and ("CODE_REPO" in env or "CLONE_FROM" in env) and gate_repo.exists():
        env["TEROK_GATE_SOCKET"] = f"{_CONTAINER_RUNTIME_DIR}/gate-server.sock"

    return env, volumes


# ---------- Git identity ----------


def resolve_git_identity(
    agent_name: str,
    agent_email: str,
    human_name: str,
    human_email: str,
    authorship: str = "agent-human",
) -> dict[str, str]:
    """Resolve ``GIT_AUTHOR_*`` and ``GIT_COMMITTER_*`` env vars.

    Mirrors the logic in ``terok-env-git-identity.sh`` so that the identity
    is baked into the container environment at launch time.  This makes
    git commits work for any code path — interactive CLI wrappers, ACP
    adapters launched by toad, and headless runs — without relying on
    shell functions that only run in login shells.

    The CLI wrapper functions still call ``_terok_apply_git_identity()``
    in subshells, which overrides these env vars per invocation.  That
    gives per-agent identity when multiple agents share a container.
    """
    match authorship:
        case "human-agent":
            author_name, author_email = human_name, human_email
            committer_name, committer_email = agent_name, agent_email
        case "agent":
            author_name, author_email = agent_name, agent_email
            committer_name, committer_email = agent_name, agent_email
        case "human":
            author_name, author_email = human_name, human_email
            committer_name, committer_email = human_name, human_email
        case _:  # agent-human (default)
            author_name, author_email = agent_name, agent_email
            committer_name, committer_email = human_name, human_email

    return {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
    }


def apply_git_identity_env(
    env: dict[str, str],
    project: ProjectConfig,
    agent_name: str = "AI Agent",
    agent_email: str = "ai-agent@localhost",
) -> None:
    """Add ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` to a container env dict.

    Uses the project's authorship policy and human identity together with
    the given agent identity to resolve the four git env vars.
    """
    env.update(
        resolve_git_identity(
            agent_name=agent_name,
            agent_email=agent_email,
            human_name=project.human_name or "Nobody",
            human_email=project.human_email or "nobody@localhost",
            authorship=project.git_authorship,
        )
    )


# ---------- Vault ----------
#
# The per-container supervisor (terok-sandbox) embeds a vault proxy per
# container, started by the OCI hook at container start.  The supervisor
# reads its sidecar JSON at hook fire time and stands the proxy up before
# the container's first egress, so there is no host-side step here.


def _apply_claude_oauth_overrides(env: dict[str, str]) -> None:
    """Adjust Claude OAuth env vars based on the experimental proxy config.

    Executor handles all generic proxy plumbing (phantom tokens, transport,
    SSH agent).  This function only adjusts Claude-specific env vars:

    - **Proxied** (``is_claude_oauth_proxied``): remove phantom token, keep
      ``ANTHROPIC_BASE_URL`` — the container uses the mounted
      ``.credentials.json`` marker directly with the proxy.
    - **Skipped** (default): remove all Claude proxy env vars — Claude Code's
      hardcoded ``BASE_API_URL`` bypasses the proxy anyway.
    - **Exposed** (``expose_oauth_token``): also removes vars — the real
      OAuth token is mounted directly for Claude Code subscription features.
    """
    from ..core.config import is_claude_oauth_proxied

    # Only act when executor injected Claude OAuth vars
    if "CLAUDE_CODE_OAUTH_TOKEN" not in env:
        return

    if is_claude_oauth_proxied():
        # Proxied: remove phantom token (the mounted .credentials.json
        # marker is used for auth), keep ANTHROPIC_BASE_URL for routing
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    else:
        # Skipped or exposed: remove all Claude proxy env vars
        for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_UNIX_SOCKET"):
            env.pop(key, None)


def _shared_config_patch_providers(roster: AgentRoster) -> frozenset[str]:
    """Return providers that declare shared config patches in the roster."""
    return frozenset(
        name for name, route in roster.vault_routes.items() if route.shared_config_patch
    )


def _vault_patch_provider_sets(
    roster: AgentRoster, *, vault_bypass: bool = False
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(enabled, disabled)`` shared-config patch provider sets.

    Enabled providers have their roster-declared patches applied.  Disabled
    providers have previously managed values reconciled away if terok still
    owns them.  Codex is special only in its feature gate: the secure
    vaulted OAuth mode enables the shared ``~/.codex/config.toml`` rewrite;
    disabled/exposed/bypassed modes remove stale managed Codex URLs.
    """
    from ..core.config import is_codex_oauth_proxied

    providers = _shared_config_patch_providers(roster)
    if vault_bypass:
        return frozenset(), providers

    enabled = providers
    disabled: frozenset[str] = frozenset()
    if not is_codex_oauth_proxied():
        enabled -= {"codex"}
        disabled |= providers & {"codex"}
    return enabled, disabled


def _warn_leaked_credentials(mounts_dir: Path) -> None:
    """Warn about real credential files in shared mounts.

    When an OAuth token is intentionally exposed (for Claude subscription
    features or direct Codex control), the
    provider-specific leak warning is suppressed and replaced by a loud,
    explicit banner so the user can't miss that a real token is mounted.

    *mounts_dir* is the project's effective agent-config mount tree —
    shared or project-scoped per
    [`Project.mounts_dir`][terok.lib.domain.project.Project.mounts_dir].
    """
    import sys

    from terok.lib.integrations.executor import scan_leaked_credentials

    from ..core.config import is_claude_oauth_exposed, is_codex_oauth_exposed
    from ..util.ansi import bold, supports_color, yellow

    leaked = scan_leaked_credentials(mounts_dir)
    color = supports_color()

    def _banner(provider_label: str, file_desc: str) -> None:
        print(
            "\n"
            + bold(
                yellow(
                    f"  WARNING: {provider_label} OAuth token is EXPOSED to all task containers.\n"
                    f"  The vault does NOT protect this token — it is mounted\n"
                    f"  directly via {file_desc} in the shared config directory.\n"
                    f"  Every task container managed by terok can read the real token.\n",
                    color,
                ),
                color,
            ),
            file=sys.stderr,
        )

    if is_claude_oauth_exposed():
        _banner("Claude", ".credentials.json")
        leaked = [(p, path) for p, path in leaked if p != "claude"]

    if is_codex_oauth_exposed():
        _banner("Codex", "auth.json")
        leaked = [(p, path) for p, path in leaked if p != "codex"]

    for provider, path in leaked:
        _logger.warning("Real credential in shared mount for provider %s", provider)
        _logger.debug("  path: %s", path)


# ---------- Clone-cache workspace seeding ----------


def _seed_workspace_cache(repo_dir: Path, project_id: str, code_repo: str | None) -> None:
    """Pre-populate *repo_dir* from the clone cache (best-effort).

    Only acts when the workspace has a ``.new-task-marker`` (new task)
    and no existing ``.git``.  Failures are logged and swallowed — the
    container falls back to a full ``git clone``.
    """
    if (repo_dir / ".git").is_dir() or not (repo_dir / ".new-task-marker").is_file():
        return

    try:
        from terok.lib.integrations.executor import seed_workspace_from_clone_cache
    except ImportError:
        return

    try:
        seed_workspace_from_clone_cache(
            repo_dir, project_id, origin_url=code_repo, cfg=make_sandbox_config()
        )
    except Exception:
        _logger.warning(
            "seed_workspace_from_clone_cache failed for project %s at %s",
            project_id,
            repo_dir,
            exc_info=True,
        )


# ---------- Main builder ----------


@dataclass(frozen=True)
class TaskEnvironment:
    """Per-task container env + volume builder.

    Holds the ``(project, task_id)`` pair the env composition needs.
    [`materialize`][terok.lib.orchestration.environment.TaskEnvironment.materialize]
    walks the full pipeline — gate-URL resolution, workspace cache
    seeding, git-identity resolution, executor-shared assembly,
    terok-specific env overlays — and returns ``(env, volumes)`` ready
    for ``RunSpec`` construction.

    In **sealed** isolation mode (``project.is_sealed``) volumes are
    injected via ``podman cp`` instead of bind mounts — the sandbox
    handles this transparently when ``RunSpec.sealed`` is set.  The
    workspace is still created and cache-seeded on the host so the
    container benefits from fast startup in both modes.
    """

    project: ProjectConfig
    """Resolved project configuration the task belongs to."""

    task_id: str
    """Identifier of the task whose container is being assembled."""

    def materialize(self) -> tuple[dict, list[VolumeSpec]]:
        """Compose env + volumes for the task container.

        Delegates shared config mounts, base env vars, workspace volume,
        git identity, and OpenCode provider env to
        [`terok_executor.assemble_container_env`][terok_executor.assemble_container_env],
        then layers terok-specific concerns: ``PROJECT_ID``, gate
        server URLs, and the full vault (OAuth, socket transport, SSH
        agent).
        """
        project = self.project
        task_id = self.task_id
        sealed = project.is_sealed
        mounts_dir = project_mounts_dir(project)

        task_dir = project.tasks_root / str(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = task_dir / WORKSPACE_DANGEROUS_DIRNAME
        repo_dir.mkdir(exist_ok=True)

        from ..core.config import get_services_mode, get_vault_bypass, get_vault_transport

        cfg = make_sandbox_config()
        use_socket = get_services_mode() == "socket"

        # Pre-resolve gate server URLs → CODE_REPO / CLONE_FROM / GIT_BRANCH,
        # plus any security-mode volumes (the gate socket sub-mount).
        sec_env, sec_volumes = _security_mode_env_and_volumes(project, cfg, use_socket=use_socket)

        # Seed workspace from clone cache (fast-start optimisation).
        # Only for new tasks (marker present, no .git yet).  The in-container
        # init script then does fetch+reset instead of a full git clone.
        # In sealed mode the seeded dir is podman-cp'd into the container.
        _seed_workspace_cache(repo_dir, project.id, sec_env.get("CODE_REPO"))

        # Pre-resolve git identity using terok's authorship logic so the
        # container has correct GIT_AUTHOR_*/GIT_COMMITTER_* from launch.
        identity = resolve_git_identity(
            agent_name="AI Agent",
            agent_email="ai-agent@localhost",
            human_name=project.human_name or "Nobody",
            human_email=project.human_email or "nobody@localhost",
            authorship=project.git_authorship,
        )

        from terok.lib.integrations.executor import (
            AgentRoster,
            ContainerEnvSpec,
            assemble_container_env,
        )

        # Vault: bypass disables proxy plumbing entirely; otherwise the
        # per-container supervisor stands the proxy up on container start,
        # so there is nothing to bring up here.
        vault_bypass = get_vault_bypass()
        if not vault_bypass:
            _check_project_credentials_present(project)
        vault_transport = get_vault_transport()

        roster = AgentRoster.shared()
        enabled_patch_providers, disabled_patch_providers = _vault_patch_provider_sets(
            roster, vault_bypass=vault_bypass
        )

        result = assemble_container_env(
            ContainerEnvSpec(
                task_id=task_id,
                provider_name=project.default_agent or "claude",
                workspace_host_path=repo_dir,
                code_repo=sec_env.get("CODE_REPO"),
                clone_from=sec_env.get("CLONE_FROM"),
                branch=sec_env.get("GIT_BRANCH"),
                git_author_name=identity["GIT_AUTHOR_NAME"],
                git_author_email=identity["GIT_AUTHOR_EMAIL"],
                git_committer_name=identity["GIT_COMMITTER_NAME"],
                git_committer_email=identity["GIT_COMMITTER_EMAIL"],
                authorship=project.git_authorship,
                human_name=project.human_name or "Nobody",
                human_email=project.human_email or "nobody@localhost",
                credential_scope=project.id,
                credential_set=project.credential_set,
                vault_transport=vault_transport,
                vault_required=not vault_bypass,
                unrestricted=False,  # task_runners resolves per-provider config
                shared_dir=None if sealed else project.shared_dir,
                envs_dir=mounts_dir,
                timezone=project.timezone,
                enabled_vault_patch_providers=enabled_patch_providers,
                disabled_vault_patch_providers=disabled_patch_providers,
                expose_credential_providers=exposed_credential_providers(),
            ),
            roster,
            # bypass → skip proxy entirely (no tokens, no check)
            caller_manages_vault=vault_bypass,
        )

        env = dict(result.env)
        volumes: list[VolumeSpec] = [*result.volumes, *sec_volumes]

        # terok-specific env vars not covered by the shared assembly
        env["PROJECT_ID"] = project.id
        env["GIT_RESET_MODE"] = os.environ.get("TEROK_GIT_RESET_MODE", "none")
        # Forward every sec_env key the spec didn't already consume.  Inverted
        # from a closed allowlist after a leak: each new gate env var added
        # in _security_mode_env_and_volumes had to be redundantly listed here
        # too, and forgetting silently dropped the value (#902).
        env.update({k: v for k, v in sec_env.items() if k not in _SPEC_CONSUMED_SEC_ENV_KEYS})

        # Note: the bind-mount for the per-container /run/terok/ dir is
        # added by AgentRunner.launch_prepared — it knows the container
        # name (the per-container dir key), this layer does not.

        # Claude OAuth env override + leaked-cred scan with exposed-token filtering
        if not vault_bypass:
            _apply_claude_oauth_overrides(env)
            _warn_leaked_credentials(mounts_dir)

        return env, volumes


def build_task_env_and_volumes(
    project: ProjectConfig, task_id: str
) -> tuple[dict, list[VolumeSpec]]:
    """Shim around [`TaskEnvironment.materialize`][terok.lib.orchestration.environment.TaskEnvironment.materialize]."""
    return TaskEnvironment(project, task_id).materialize()
