# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Auth flow — host-wide or project-scoped credential acquisition.

The terok-side wrapper around [`Authenticator`][terok_executor.Authenticator].
Resolves which image to use (deferred for the host-wide path so picking
API key never triggers an L1 build), applies the OAuth-vs-API-key gates
from the user's config, and prompts the user when an L1 image needs
building.

The image-resolution logic lives in the module-private
``_resolve_host_auth_image``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from terok.lib.integrations.executor import Authenticator

from ..core.images import project_cli_image
from ..orchestration.image import image_exists


def resolve_credential_routing(project_id: str | None) -> tuple[Path, str]:
    """Resolve ``(mounts_dir, credential_set)`` for an auth flow.

    The single source of truth for credential routing, shared by the CLI
    [`authenticate`][terok.lib.domain.auth.authenticate] flow and the
    TUI's native auth path so the two can't drift.

    Host-wide auth (``project_id is None``) and ``shared``-scope projects
    land in the host-wide mount tree + the ``"default"`` vault set.  A
    ``project``-scoped project gets its private subtree + a vault set
    keyed by the project id.

    Side effect: for a ``project``-scoped project the per-project mount
    tree is created (mode ``0o700``) if absent — the OAuth post-capture
    writer needs it to exist, and other users on the host must not be
    able to enumerate the project's credential file paths.  The ``chmod``
    is explicit because ``mkdir(mode=…)`` only applies on creation.
    """
    from ..core.config import sandbox_live_mounts_dir
    from ..core.projects import load_project
    from ..orchestration.environment import project_mounts_dir

    if project_id is None:
        return sandbox_live_mounts_dir(), "default"

    project = load_project(project_id)
    mounts_dir = project_mounts_dir(project)
    if project.credentials_scope == "project":
        mounts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        mounts_dir.chmod(0o700)
    return mounts_dir, project.credential_set


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
    build.

    Credential routing follows the named project's
    [`credentials_scope`][terok.lib.core.project_model.ProjectConfig.credentials_scope]:
    ``"shared"`` (default) writes to the host-wide bucket every project
    sees, ``"project"`` carves out a private set under the project's id
    and stores the agent-config files under the project's own mount
    tree.  When *project_id* is ``None``, both default to the host-wide
    bucket — no project context exists to override them.
    """
    from ..core.config import (
        is_claude_oauth_exposed,
        is_codex_oauth_exposed,
        is_oauth_enabled_for,
    )

    expose = (provider == "claude" and is_claude_oauth_exposed()) or (
        provider == "codex" and is_codex_oauth_exposed()
    )

    image: str | Callable[[], str]
    if project_id is None:
        image = lambda: _resolve_host_auth_image(provider)  # noqa: E731 — lazy by design
    else:
        image = project_cli_image(project_id)

    mounts_dir, credential_set = resolve_credential_routing(project_id)

    # The roster declares which auth modes a provider supports; terok's
    # config can disable the OAuth path (experimental flag + per-provider
    # ``allow_oauth``).  The listing screen already filters on this; pass
    # the same gate into the executor's auth flow so the per-provider
    # prompt agrees.
    Authenticator(provider).run(
        project_id,
        mounts_dir=mounts_dir,
        image=image,
        expose_token=expose,
        oauth_enabled=is_oauth_enabled_for(provider),
        credential_set=credential_set,
    )


def find_host_auth_image(provider: str) -> str | None:
    """Return an existing L1 image suitable for host-wide *provider* auth.

    Non-interactive: returns ``None`` when no matching L1 image exists
    and the provider's OAuth path needs one.  TUI callers handle the
    missing-image case themselves (notify, push a build screen, etc.)
    rather than going through the CLI's interactive build prompt in
    ``_resolve_host_auth_image``.

    For API-key-only providers (``supports_oauth`` is False), no
    container will ever launch, so any L1 tag is fine — returns the
    default alias unconditionally.
    """
    from terok.lib.integrations.executor import (
        AUTH_PROVIDERS,
        DEFAULT_BASE_IMAGE,
        ExecutorConfigView,
        ImageBuilder,
    )

    info = AUTH_PROVIDERS.get(provider)
    needs_container = info is not None and info.supports_oauth

    base = ExecutorConfigView.image_base_image() or DEFAULT_BASE_IMAGE
    builder = ImageBuilder(base)
    default_alias = builder.l1_tag()
    per_agent = builder.l1_tag((provider,))

    # Default alias is reserved for the user's configured set; trust it
    # iff it actually contains the requested provider.
    if image_exists(default_alias) and provider in ImageBuilder.image_agents(default_alias):
        return default_alias
    if image_exists(per_agent):
        return per_agent
    if not needs_container:
        # API-key-only providers never launch a container, so any tag is fine.
        return default_alias
    return None


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

    from terok.lib.integrations.executor import (
        DEFAULT_BASE_IMAGE,
        ExecutorConfigView,
        ImageBuilder,
    )

    existing = find_host_auth_image(provider)
    if existing is not None:
        return existing

    base = ExecutorConfigView.image_base_image() or DEFAULT_BASE_IMAGE
    agents = ExecutorConfigView.image_agents() or "all"
    builder = ImageBuilder(base)
    default_alias = builder.l1_tag()
    per_agent = builder.l1_tag((provider,))

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
        ImageBuilder(base).build_base(agents=(provider,))
        return per_agent
    # Anything else (including empty input) → recommended path.
    return ImageBuilder(base).ensure_default_l1(agents)


__all__ = ["authenticate", "find_host_auth_image", "resolve_credential_routing"]
