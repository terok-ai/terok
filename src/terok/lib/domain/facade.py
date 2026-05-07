# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Service facade — stable import boundary for the terok library.

Re-exports key service classes and functions so that the presentation
layer (CLI, TUI) can import from a single stable module instead of
reaching into internal subpackages.

**Recommended entry points** for project-scoped operations::

    from terok.lib.domain.facade import get_project

    project = get_project("myproj")  # → Project (Aggregate Root)
    task = project.create_task(name="x")  # → Task (Entity)
    task.run_cli()

Factory functions:

- [`get_project`][terok.lib.domain.facade.get_project] — load a single project by ID
- [`list_projects`][terok.lib.domain.facade.list_projects] — return all known projects
- [`derive_project`][terok.lib.domain.facade.derive_project] — create a new project from an existing one

The facade also re-exports low-level service functions (``task_new``,
``task_run_cli``, ``build_images``, etc.) for callers that need direct
access without going through the ``Project`` object graph.  These are
used by CLI commands that operate on ``project_id`` strings directly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from terok_executor import (
    authenticate as _authenticate_raw,
)

if TYPE_CHECKING:
    from terok_sandbox.credentials.ssh import SSHInitResult

from ..core.images import project_cli_image
from ..core.projects import derive_project as _derive_project, load_project
from ..orchestration.image import build_images, generate_dockerfiles, image_exists
from ..orchestration.task_runners import (  # noqa: F401 — re-exported public API
    HeadlessRunRequest,
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)
from ..orchestration.tasks import (  # noqa: F401 — re-exported public API
    TaskDeleteResult,
    get_tasks,
    task_archive_list,
    task_archive_logs,
    task_delete,
    task_list,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
)
from .image_cleanup import (  # noqa: F401 — re-exported public API
    cleanup_images,
    find_orphaned_images,
    list_images,
)
from .project import (  # noqa: F401 — re-exported public API
    DeleteProjectResult,
    Project,
    delete_project,
    find_projects_sharing_gate,
    make_git_gate,
    make_ssh_manager,
)
from .project_state import get_project_state, is_task_image_old
from .task import Task  # noqa: F401 — re-exported public API
from .task_logs import LogViewOptions, task_logs  # noqa: F401 — re-exported public API
from .vault import vault_db  # noqa: F401 — re-exported public API

# ---------------------------------------------------------------------------
# Project factory functions
# ---------------------------------------------------------------------------


def get_project(project_id: str) -> Project:
    """Load a project by ID and return a rich [`Project`][terok.lib.domain.facade.Project] aggregate."""
    return Project(load_project(project_id))


def project_image_exists(project_id: str) -> bool:
    """Return ``True`` when the project's L2 CLI image is present locally."""
    return image_exists(project_cli_image(project_id))


def list_projects() -> list[Project]:
    """Return all known projects as rich [`Project`][terok.lib.domain.facade.Project] aggregates."""
    from ..core.projects import list_projects as _list_projects

    return [Project(cfg) for cfg in _list_projects()]


def derive_project(source_id: str, new_id: str) -> Project:
    """Copy *source_id*'s gate mirror and vault SSH assignments under *new_id*."""
    _derive_project(source_id, new_id)
    _share_ssh_key_assignments(source_id, new_id)
    return Project(load_project(new_id))


def _share_ssh_key_assignments(source_id: str, new_id: str) -> None:
    """Copy every SSH key assignment from *source_id* to *new_id*."""
    with vault_db() as db:
        for row in db.list_ssh_keys_for_scope(source_id):
            db.assign_ssh_key(new_id, row.id)


# ---------------------------------------------------------------------------
# SSH provisioning — the three public verbs form the user-facing ``ssh-init``
# story: mint the keypair, bind it to the project, render the result for the
# human.  ``maybe_pause_for_ssh_key_registration`` is the follow-up step for
# projects that need the public key registered upstream before gate-sync.
# ---------------------------------------------------------------------------


def provision_ssh_key(
    project_id: str,
    *,
    key_type: str = "ed25519",
    comment: str | None = None,
    force: bool = False,
) -> SSHInitResult:
    """Mint a vault-backed keypair for *project_id* and bind it to the project (scope).

    Single entry point for both the CLI and the TUI.  Rendering the
    result for the user is the caller's job — see [`summarize_ssh_init`][terok.lib.domain.facade.summarize_ssh_init].
    """
    from .project import make_ssh_manager

    project = load_project(project_id)
    with make_ssh_manager(project) as ssh:
        result = ssh.init(key_type=key_type, comment=comment, force=force)
    register_ssh_key(project_id, result["key_id"])
    return result


def register_ssh_key(project_id: str, key_id: int) -> None:
    """Bind an already-minted *key_id* to *project_id* (idempotent)."""
    with vault_db() as db:
        db.assign_ssh_key(project_id, key_id)


def summarize_ssh_init(result: SSHInitResult) -> None:
    """Render an ``ssh-init`` result for the terminal."""
    print(f"  id:          {result['key_id']}")
    print(f"  type:        {result['key_type']}")
    print(f"  fingerprint: {result['fingerprint']}")
    print(f"  comment:     {result['comment']}")
    print("Public key (register as a deploy key on the remote):")
    print(f"  {result['public_line']}")


def project_needs_key_registration(project_id: str) -> bool:
    """Return True when the project's upstream is SSH-scheme, so a deploy key must be added.

    Shared predicate used by the CLI's pause helper below and the TUI
    wizard's mid-flow "continue" gate — keeps the rule (SSH URLs need
    registration, HTTPS and no-upstream projects don't) in one place.
    """
    from terok_sandbox import is_ssh_url

    try:
        project = load_project(project_id)
    except SystemExit:
        return False
    return bool(project.upstream_url) and is_ssh_url(project.upstream_url)


def maybe_pause_for_ssh_key_registration(project_id: str) -> None:
    """Pause so the user can register the deploy key, but only for SSH upstreams."""
    if project_needs_key_registration(project_id):
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Add the public key shown above as a")
        print("deploy key (or to your SSH keys) on the git remote.")
        print("=" * 60)
        input("Press Enter once the key is registered... ")


def authenticate(provider: str, project_id: str | None = None) -> None:
    """Run the auth flow for *provider*, host-wide by default.

    When *project_id* is given, the project's L2 CLI image is reused — the
    escape hatch for users who want project-scoped credentials or happen to
    have a project image handy.  When omitted, terok resolves an L1 image
    (shared across projects that build on the same base) and offers to
    build one if none exists — the "fresh install, no project yet" path.

    Image resolution is **deferred**: the executor only invokes the
    resolver after the user has chosen the OAuth path from the
    OAuth-vs-API-key prompt, so picking API key never triggers an L1
    build.  Vault storage is provider-scoped in both modes, so switching
    from a per-project auth to a host-wide one later (or vice versa)
    does not duplicate or overwrite credentials.
    """
    from ..core.config import (
        is_claude_oauth_exposed,
        is_codex_oauth_exposed,
        sandbox_live_mounts_dir,
    )

    expose = (provider == "claude" and is_claude_oauth_exposed()) or (
        provider == "codex" and is_codex_oauth_exposed()
    )

    image: str | Callable[[], str]
    if project_id is None:
        image = lambda: _resolve_host_auth_image(provider)  # noqa: E731 — lazy by design
    else:
        image = project_cli_image(project_id)

    _authenticate_raw(
        project_id,
        provider,
        mounts_dir=sandbox_live_mounts_dir(),
        image=image,
        expose_token=expose,
    )


def _resolve_host_auth_image(provider: str) -> str:
    """Pick (or build) an L1 image suitable for host-wide ``terok auth``.

    Reads ``image.base_image`` and ``image.agents`` from the user's
    global config so a Fedora-projects user doesn't end up with a stray
    ``terok-l1-cli:ubuntu-24.04`` just for auth.  The L1 default-alias
    is *only* set by builds invoked with ``tag_as_default=True``, so if
    it exists we know it contains the user's configured agent set —
    we still verify via the ``ai.terok.agents`` OCI label and fall
    through to a build if the alias is stale.

    When a build is required, the user gets a three-way prompt:

    - **[Y]** (default, recommended) — build the full default L1 with
      every agent the user has enabled.  Subsequent ``terok auth``
      calls for any other provider share this image.
    - **[1]** — build a minimal per-agent L1.  Cheaper if the user
      truly only needs one provider authenticated.
    - **[n]** — abort with a hint about how to build manually.
    """
    import sys

    from rich.console import Console
    from terok_executor import (
        AUTH_PROVIDERS,
        build_base_images,
        ensure_default_l1,
        image_agents,
        l1_image_tag,
    )

    from ..core.config import (
        get_global_image_agents,
        get_global_image_base_image,
    )

    info = AUTH_PROVIDERS.get(provider)
    needs_container = info is not None and info.supports_oauth

    base = get_global_image_base_image()
    agents = get_global_image_agents()
    default_alias = l1_image_tag(base)
    per_agent = l1_image_tag(base, agents=(provider,))

    # Default alias is reserved for the user's configured set; trust it
    # iff it actually contains the requested provider.
    if image_exists(default_alias) and provider in image_agents(default_alias):
        return default_alias
    if image_exists(per_agent):
        return per_agent
    if not needs_container:
        # API-key-only providers never launch a container, so any tag is fine.
        return default_alias

    hint = (
        "No agent image present.  Build one with: terok image build "
        "(or terok project build <project>), "
        "or pass --project <id> to reuse an existing project's image."
    )
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit(hint)

    # ``highlight=False`` keeps Rich's default highlighter from auto-styling
    # the parentheses, version numbers, and brackets in our literal text —
    # we already mark up exactly what we want emphasized.
    err = Console(stderr=True, highlight=False)
    err.print(f"\nAuth for [bold]{provider}[/bold] needs an agent image.  Build now?")
    err.print(
        f"  [bold green]\\[Y][/bold green] (default, recommended) "
        f"full L1 with every configured agent — [dim]{default_alias}[/dim]"
    )
    err.print(
        f"  [bold yellow]\\[1][/bold yellow] minimal L1 with just "
        f"[bold]{provider}[/bold] — [dim]{per_agent}[/dim]"
    )
    err.print("  [bold red]\\[n][/bold red] abort")
    try:
        answer = input("[Y/1/n]: ").strip().lower()
    except EOFError:
        print()
        raise SystemExit(hint) from None
    except KeyboardInterrupt:
        print()
        raise SystemExit(130) from None

    if answer in ("n", "no"):
        raise SystemExit(hint)
    if answer == "1":
        build_base_images(base, agents=(provider,))
        return per_agent
    # Anything else (including empty input) → recommended path.
    return ensure_default_l1(base, agents=agents)


__all__ = [
    # Project factory functions
    "get_project",
    "list_projects",
    "derive_project",
    # Rich domain objects
    "Project",
    "Task",
    # Image management
    "generate_dockerfiles",
    "build_images",
    "project_image_exists",
    # Image listing & cleanup
    "list_images",
    "find_orphaned_images",
    "cleanup_images",
    # Project lifecycle
    "delete_project",
    "DeleteProjectResult",
    # Task lifecycle
    "TaskDeleteResult",
    "task_new",
    "task_delete",
    "task_rename",
    "task_login",
    "task_list",
    "task_status",
    "task_stop",
    "task_archive_list",
    "task_archive_logs",
    "get_tasks",
    # Task runners
    "task_run_cli",
    "task_run_toad",
    "task_run_headless",
    "HeadlessRunRequest",
    "task_restart",
    "task_followup_headless",
    # Task logs
    "task_logs",
    "LogViewOptions",
    # Security setup
    "make_ssh_manager",
    "make_git_gate",
    # Workflow helpers
    "provision_ssh_key",
    "register_ssh_key",
    "summarize_ssh_init",
    "vault_db",
    "maybe_pause_for_ssh_key_registration",
    "project_needs_key_registration",
    # Auth
    "authenticate",
    # Project state
    "get_project_state",
    "is_task_image_old",
    "find_projects_sharing_gate",
]
