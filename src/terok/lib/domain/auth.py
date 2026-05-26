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

from terok.lib.integrations.executor import Authenticator

from ..core.images import project_cli_image
from ..orchestration.image import image_exists


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
        is_oauth_enabled_for,
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

    # The roster declares which auth modes a provider supports; terok's
    # config can disable the OAuth path (experimental flag + per-provider
    # ``allow_oauth``).  The listing screen already filters on this; pass
    # the same gate into the executor's auth flow so the per-provider
    # prompt agrees.
    Authenticator(provider).run(
        project_id,
        mounts_dir=sandbox_live_mounts_dir(),
        image=image,
        expose_token=expose,
        oauth_enabled=is_oauth_enabled_for(provider),
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

    from terok.lib.integrations.executor import (
        AUTH_PROVIDERS,
        DEFAULT_BASE_IMAGE,
        ExecutorConfigView,
        ImageBuilder,
    )

    info = AUTH_PROVIDERS.get(provider)
    needs_container = info is not None and info.supports_oauth

    base = ExecutorConfigView.image_base_image() or DEFAULT_BASE_IMAGE
    agents = ExecutorConfigView.image_agents() or "all"
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


__all__ = ["authenticate"]
