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
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from terok.lib.integrations.sandbox import (
    CONTAINER_GATE_SOCKET,
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
    from terok.lib.integrations.executor import AgentRoster, EgressProjection

_logger = logging.getLogger(__name__)

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

    The shared-scope flow tolerates a missing credential at launch: the
    host-wide bucket serves every project, a project may hold agents its
    user never runs, and the bucket can be filled later.  It does not
    tolerate it *inside* the task, though — in-container login is no
    fallback, because the egress firewall reserves every vault-relayed
    provider host (``api.anthropic.com`` and the like) for the vault, so an
    agent started without a stored credential fails to connect.  The
    launch therefore warns instead (see
    [`_warn_unready_default_agent`][terok.lib.orchestration.environment._warn_unready_default_agent]).
    A project that opted into ``credentials_scope: project`` has
    explicitly *isolated* its bucket: an empty one is almost always an
    oversight (they edited project.yml but forgot
    ``terok auth --project <name>``), so it fails before the container
    starts, naming the recovery command.
    """
    if project.credentials_scope != "project":
        return

    from terok.lib.integrations.executor import list_authenticated_agents

    if list_authenticated_agents(scope=project.credential_set):
        return

    agent = project.default_agent or "claude"
    raise SystemExit(
        f"Project '{project.name}' is configured for per-project credentials "
        f"(credentials.scope: project) but its vault bucket is empty.\n"
        f"Authenticate before launching tasks:\n\n"
        f"  terok auth {agent} --project {project.name}\n"
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


def _task_gate_token(project_name: str, task_id: str) -> str:
    """Return the task's gate token, minting and persisting it on first use.

    Task-scoped, not container-scoped: the restart ladder's recreate rung
    builds a fresh container while the workspace keeps the origin URL a
    previous generation wrote into ``.git/config`` — a per-container
    token would strand every recreated task on a dead credential (403
    from the gate).  Persisting the token in the task meta (next to the
    toad ``web_token``) keeps one token valid for the task's whole life;
    it dies with the task's metadata.  The sidecar JSON already carries
    the token in plaintext on the host, so this adds no new exposure.
    """
    from .tasks import load_task_meta, write_task_state

    meta, handle = load_task_meta(project_name, task_id)
    token = meta.get("gate_token")
    if not token:
        token = mint_gate_token()
        meta["gate_token"] = token
        write_task_state(handle, meta)
    return str(token)


def _gatekeeping_repo_env(
    project: ProjectConfig,
    gate_base: Path,
    gate_port: int,
    token_source: Callable[[], str],
) -> dict[str, str]:
    """Repo-access env for a gatekeeping-class task — the gate *is* origin.

    The container clones and pushes through the gate, so a missing gate
    mirror is fatal.  Resolves the task's persistent gate token via
    *token_source*, embeds it in the gate URL, and exposes it via
    ``TEROK_GATE_TOKEN`` so the per-container supervisor can validate
    the requests it receives.
    """
    gate_repo = project.gate_path
    if not gate_repo.exists():
        raise SystemExit(
            f"Git gate missing for project '{project.name}'.\n"
            f"Expected at: {gate_repo}\n"
            f"Run 'terok project gate-sync {project.name}' to create/update the local mirror."
        )
    token = token_source()
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
    token_source: Callable[[], str],
) -> dict[str, str]:
    """Repo-access env for an online-class task — clones from upstream directly.

    The gate is used only as an opt-in clone accelerator
    (``gate.enabled``) when its mirror exists; ``gate.enabled: false``
    is the escape hatch for hosts that cannot use the gate.  When the
    gate is in play it resolves the task's persistent token via
    *token_source*, embeds it in the gate URL, and exposes it via
    ``TEROK_GATE_TOKEN`` for the per-container supervisor to validate.
    """
    env: dict[str, str] = {}
    gate_repo = project.gate_path
    if project.gate_enabled and gate_repo.exists():
        token = token_source()
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
    token_source: Callable[[], str],
    *,
    use_socket: bool = False,
) -> tuple[dict[str, str], list[VolumeSpec]]:
    """Return env vars and volumes for the project's security mode.

    *token_source* supplies the gate token lazily — it is only called
    (and thus only persisted into the task meta) when the mode actually
    wires a gate URL.
    """
    volumes: list[VolumeSpec] = []
    gate_base = cfg.gate_base_path
    gate_port = _resolve_gate_port()

    if project.security_class == "gatekeeping":
        env = _gatekeeping_repo_env(project, gate_base, gate_port, token_source)
    else:
        env = _online_repo_env(project, gate_base, gate_port, token_source)

    # Gate socket path for the container-side socat bridge.  The gate runs
    # inside the per-container supervisor.  The executor mounts the isolated
    # runtime tree, and the supervisor binds the gate endpoint in its
    # dedicated lane.  No host bind-mount is needed here; the bridge only
    # needs the canonical in-container socket path.
    if use_socket and "TEROK_GATE_TOKEN" in env:
        env["TEROK_GATE_SOCKET"] = CONTAINER_GATE_SOCKET

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


_PROVIDER_HANDLE_PREFIX = "TEROK_PROVIDER_"
"""Leading fragment of the executor's per-provider handle variables.

The executor materializes ``TEROK_PROVIDER_<NAME>_TOKEN`` (plus label,
models and one ``_BASE_<PROTOCOL>`` per served protocol) for every provider
a task can reach; the in-container wrappers and the ``providers`` view read
a provider as ready exactly when its token variable is set."""

_CLAUDE_ENTRY = "claude"
"""The auth entry whose vault login the Claude OAuth modes govern."""


def _provider_handle_prefix(provider: str) -> str:
    """The shared prefix of *provider*'s handle variables (``TEROK_PROVIDER_<NAME>_``)."""
    return f"{_PROVIDER_HANDLE_PREFIX}{provider.upper()}_"


def _credential_provider(roster: AgentRoster, entry: str) -> str | None:
    """The provider *entry*'s stored credential authenticates, or ``None`` for no such entry."""
    info = roster.auth_providers.get(entry)
    if info is None:
        return None
    return info.credential_provider or entry


def _apply_claude_oauth_overrides(env: dict[str, str]) -> bool:
    """Adjust Claude OAuth env vars based on the experimental proxy config.

    Executor handles all generic proxy plumbing (phantom tokens, transport,
    SSH agent).  This function only adjusts Claude-specific env vars:

    - **Proxied** (``is_claude_oauth_proxied``): remove phantom token, keep
      ``ANTHROPIC_BASE_URL`` — the container uses the mounted
      ``.credentials.json`` marker directly with the proxy.
    - **Skipped** (default): remove all Claude proxy env vars — Claude Code's
      hardcoded ``BASE_API_URL`` bypasses the proxy anyway.  The provider
      handle the executor materialized for the same login
      (``TEROK_PROVIDER_<NAME>_*``) stays: it belongs to the whole task,
      and every agent that reaches the provider through the vault reads it.
      claude alone cannot use the login here, so the task also carries the
      executor's claude-only "OAuth login not routed" signal: a bare
      ``claude`` refuses up front with the cause and its remedies instead of
      running into the firewall's refusal of the provider's hosts
      (terok-ai/terok#1273), and the launch names it on the host too
      ([`_warn_unready_default_agent`][terok.lib.orchestration.environment._warn_unready_default_agent]).
    - **Exposed** (``expose_oauth_token``): also removes the vars — the real
      OAuth token is mounted directly for Claude Code subscription features;
      the handle, which the executor points at the real upstream, stays too.

    Returns ``True`` when the vault holds a Claude OAuth login this
    configuration does not route to claude (the skipped mode), so the launch
    can say why claude cannot connect.
    """
    from ..core.config import is_claude_oauth_exposed, is_claude_oauth_proxied

    # Only act when executor injected Claude OAuth vars
    if "CLAUDE_CODE_OAUTH_TOKEN" not in env:
        return False

    if is_claude_oauth_proxied():
        # Proxied: remove phantom token (the mounted .credentials.json
        # marker is used for auth), keep ANTHROPIC_BASE_URL for routing
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        return False

    # Skipped or exposed: remove all Claude proxy env vars
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_UNIX_SOCKET"):
        env.pop(key, None)
    if is_claude_oauth_exposed():
        return False
    # Skipped: the login is OAuth and nothing routes it to claude.  Say so to
    # claude's own wrapper, which would otherwise pass on the task-wide handle.
    from terok.lib.integrations.executor import CLAUDE_OAUTH_UNROUTED_ENV, CLAUDE_OAUTH_UNROUTED_ON

    env[CLAUDE_OAUTH_UNROUTED_ENV] = CLAUDE_OAUTH_UNROUTED_ON
    return True


def _auth_entry_for(roster: AgentRoster, agent: str, provider: str) -> str:
    """The ``terok auth`` entry that stores *provider*'s credential, preferring *agent*'s own."""
    if _credential_provider(roster, agent) == provider:
        return agent
    for entry in roster.auth_providers:
        if _credential_provider(roster, entry) == provider:
            return entry
    return provider


def _warn_unready_default_agent(
    env: dict[str, str],
    roster: AgentRoster,
    project: ProjectConfig,
    selected_provider: str | None,
    *,
    oauth_skipped: bool,
) -> None:
    """Warn on stderr when the task's default agent cannot authenticate.

    The fresh-install trap of terok-ai/terok#1273: ``claude`` launched
    before ``terok auth claude`` cannot log in from inside the task —
    the egress firewall keeps every vault-relayed provider host for the
    vault — and used to fail with a bare ConnectionRefused.  The agent's
    wrapper now refuses with a hint, but only once someone runs it; this
    says so at launch, on the host, where the fix is.

    The check reads the task's own environment, the way the in-container
    one does: a provider is ready when its handle token
    (``TEROK_PROVIDER_<NAME>_TOKEN``) is set — stored in the vault, or
    exposed — after the OAuth overrides have had their say.
    *selected_provider* (the run's or the project's) wins over the agent's
    default provider, as it does in the wrapper.  An agent with neither —
    a harness that picks its provider at run time — is not checked.

    One case the handle cannot tell: with *oauth_skipped*, the vault holds a
    Claude OAuth login this configuration does not route to claude.  The
    handle stays — it is the task's, read by every agent that reaches the
    provider through the vault — so claude learns it from the executor's
    claude-only signal instead, and its wrapper refuses a bare ``claude``
    up front.  That gets its own warning here too, whatever the handle says,
    so the operator hears it on the host before anyone runs the agent.  Advisory only: a task
    may run other agents, and its user may authenticate and recreate it.
    """
    from ..util.ansi import supports_color, yellow

    agent = project.default_agent or _CLAUDE_ENTRY
    provider = selected_provider
    if not provider:
        binding = getattr(roster.agents.get(agent), "provider_binding", None)
        provider = getattr(binding, "default", None)
    if not provider:
        return
    unrouted = (
        oauth_skipped
        and agent == _CLAUDE_ENTRY
        and not selected_provider
        and provider == _credential_provider(roster, _CLAUDE_ENTRY)
    )
    if not unrouted and f"{_provider_handle_prefix(provider)}TOKEN" in env:
        return
    command = f"terok auth {_auth_entry_for(roster, agent, provider)}"
    if project.credentials_scope == "project":
        command += f" --project {project.name}"
    if unrouted:
        lines = [
            "Warning: claude cannot use the Claude OAuth login the vault holds: this "
            "configuration does not route it (that needs experimental: true and "
            "agent.claude.allow_oauth: true), so claude will fail to connect.",
            f"  Store an API key on the host instead:  {command}  (or turn OAuth routing on)",
        ]
    else:
        lines = [
            f"Warning: {agent} has no credential for its provider {provider!r} in this task, "
            "so it will refuse to start.",
            f"  Authenticate on the host:  {command}",
        ]
    lines += [
        "  In-container login cannot stand in: the egress firewall keeps the provider's hosts "
        "for the vault.",
        "  A task created before you authenticate needs recreating to pick the credential up.",
    ]
    print(yellow("\n".join(lines), supports_color()), file=sys.stderr)


def _shared_config_patch_providers(roster: AgentRoster) -> frozenset[str]:
    """Return the auth-provider names (agents AND tools) whose vault route carries
    a shared config patch.

    Keyed by auth-provider name to match executor's
    ``apply_shared_config_patches`` (which iterates auth providers, covering
    tools like ``gh`` that aren't in ``roster.agents``) and the codex
    feature-gate special-case in
    [`_vault_patch_provider_sets`][terok.lib.orchestration.environment._vault_patch_provider_sets].
    The patch rides on each entry's vault route (sourced from its binding).
    """
    return frozenset(
        name
        for name, auth in roster.auth_providers.items()
        if (route := roster.vault_routes.get(auth.credential_provider or name))
        and route.shared_config_patch
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


def _report_leaked_credentials(mounts_dir: Path) -> None:
    """Report real credential files found in shared mounts.

    A real credential in a shared mount is a containment failure — every
    task container can read it — so each finding is printed as a red
    error.  The deliberate exceptions are the explicitly enabled
    exposed-OAuth modes (Claude subscription features, direct Codex
    control): those suppress the error and print a loud yellow banner
    instead, so the user still can't miss that a real token is mounted.

    *mounts_dir* is the project's effective agent-config mount tree —
    shared or project-scoped per
    [`Project.mounts_dir`][terok.lib.domain.project.Project.mounts_dir].
    """
    import sys

    from terok.lib.integrations.executor import scan_leaked_credentials

    from ..core.config import is_claude_oauth_exposed, is_codex_oauth_exposed
    from ..util.ansi import bold, red, supports_color, yellow

    leaked = scan_leaked_credentials(mounts_dir)
    color = supports_color()

    def _block(paint: Callable[[str, bool], str], text: str) -> None:
        print("\n" + bold(paint(text, color), color), file=sys.stderr)

    def _exposed_banner(provider_label: str, file_desc: str) -> None:
        _block(
            yellow,
            f"  WARNING: {provider_label} OAuth token is EXPOSED to all task containers.\n"
            f"  The vault does NOT protect this token — it is mounted\n"
            f"  directly via {file_desc} in the shared config directory.\n"
            f"  Every task container managed by terok can read the real token.\n",
        )

    if is_claude_oauth_exposed():
        _exposed_banner("Claude", ".credentials.json")
        leaked = [(p, path) for p, path in leaked if p != "claude"]

    if is_codex_oauth_exposed():
        _exposed_banner("Codex", "auth.json")
        leaked = [(p, path) for p, path in leaked if p != "codex"]

    if leaked:
        providers = ", ".join(sorted({p for p, _ in leaked}))
        _block(
            red,
            f"  ERROR: Real credential in shared mount for provider(s): {providers}.\n"
            "  Every task container managed by terok can read these files —\n"
            "  the vault does NOT protect them.\n"
            "  Run `terok vault clean` to remove leaked credential files.\n",
        )
        for provider, path in leaked:
            _logger.debug("Leaked credential for %s: %s", provider, path)


# ---------- Clone-cache workspace seeding ----------


def _seed_workspace_cache(repo_dir: Path, project_name: str, code_repo: str | None) -> None:
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
            repo_dir, project_name, origin_url=code_repo, cfg=make_sandbox_config()
        )
    except Exception:
        _logger.warning(
            "seed_workspace_from_clone_cache failed for project %s at %s",
            project_name,
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

    provider_override: str | None = None
    """Per-run LLM endpoint provider override (headless ``--provider``).

    Falls back to [`ProjectConfig.default_provider`][terok.lib.core.project_model.ProjectConfig]
    (already global-resolved) when ``None``."""

    agent_config_dir: Path | None = None
    """Prepared task instructions and wrappers for executor-managed mounts."""

    def materialize(self) -> tuple[dict, list[VolumeSpec], EgressProjection]:
        """Create the environment, volumes, and egress configuration for the task.

        [`terok_executor.assemble_container_env`][terok_executor.assemble_container_env]
        adds shared config mounts, base environment variables, the workspace
        volume, the Git identity, and the LLM provider variables. The method
        adds Terok settings after the executor settings. The Terok settings
        include ``PROJECT_NAME``, gate server URLs, and vault variables for
        OAuth, socket transport, and the SSH agent.
        """
        project = self.project
        task_id = self.task_id
        sealed = project.is_sealed
        mounts_dir = project_mounts_dir(project)

        task_dir = project.tasks_root / str(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = task_dir / WORKSPACE_DANGEROUS_DIRNAME
        repo_dir.mkdir(exist_ok=True)

        from ..core.config import get_vault_bypass, vault_transport_for

        cfg = make_sandbox_config(project)
        use_socket = project.services_mode == "socket"

        # Pre-resolve gate server URLs → CODE_REPO / CLONE_FROM / GIT_BRANCH,
        # plus any security-mode volumes (the gate socket sub-mount).
        sec_env, sec_volumes = _security_mode_env_and_volumes(
            project,
            cfg,
            lambda: _task_gate_token(project.name, task_id),
            use_socket=use_socket,
        )

        # Seed workspace from clone cache (fast-start optimisation).
        # Only for new tasks (marker present, no .git yet).  The in-container
        # init script then does fetch+reset instead of a full git clone.
        # In sealed mode the seeded dir is podman-cp'd into the container.
        _seed_workspace_cache(repo_dir, project.name, sec_env.get("CODE_REPO"))

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
        vault_transport = vault_transport_for(project.services_mode)

        roster = AgentRoster.load()
        enabled_patch_providers, disabled_patch_providers = _vault_patch_provider_sets(
            roster, vault_bypass=vault_bypass
        )

        result = assemble_container_env(
            ContainerEnvSpec(
                task_id=task_id,
                agent_name=project.default_agent or "claude",
                provider=self.provider_override or project.default_provider,
                agent_config_dir=self.agent_config_dir,
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
                credential_scope=project.name,
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
        env["PROJECT_NAME"] = project.name
        env["GIT_RESET_MODE"] = os.environ.get("TEROK_GIT_RESET_MODE", "none")
        # Forward every sec_env key the spec didn't already consume.  Inverted
        # from a closed allowlist after a leak: each new gate env var added
        # in _security_mode_env_and_volumes had to be redundantly listed here
        # too, and forgetting silently dropped the value (#902).
        env.update({k: v for k, v in sec_env.items() if k not in _SPEC_CONSUMED_SEC_ENV_KEYS})

        # Note: the bind-mount for the per-container /run/terok/ dir is
        # added by AgentRunner.launch_prepared — it knows the container
        # name (the per-container dir key), this layer does not.

        # Claude OAuth env override + leaked-cred scan with exposed-token filtering,
        # then the launch-time hint for a default agent the task cannot authenticate.
        if not vault_bypass:
            oauth_skipped = _apply_claude_oauth_overrides(env)
            _report_leaked_credentials(mounts_dir)
            _warn_unready_default_agent(
                env,
                roster,
                project,
                self.provider_override or project.default_provider,
                oauth_skipped=oauth_skipped,
            )

        # Executor's roster egress projection (shield's generated t20 / t30 tiers)
        # rides alongside env/volumes to the launch path — terok forwards it
        # opaquely; it owns no egress logic itself.
        return env, volumes, result.egress


def build_task_env_and_volumes(
    project: ProjectConfig,
    task_id: str,
    *,
    provider: str | None = None,
    agent_config_dir: Path | None = None,
) -> tuple[dict, list[VolumeSpec], EgressProjection]:
    """Shim around [`TaskEnvironment.materialize`][terok.lib.orchestration.environment.TaskEnvironment.materialize]."""
    return TaskEnvironment(
        project, task_id, provider_override=provider, agent_config_dir=agent_config_dir
    ).materialize()
