# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``auth`` top-level command — authenticate an agent or tool.

Three invocation shapes, in increasing specificity:

- ``terok auth``                         — interactive chained menu.
- ``terok auth <provider>``              — host-wide auth for one provider.
- ``terok auth <provider> --project name`` — project-scoped auth.

Where credentials land depends on the named project's
[`credentials_scope`][terok.lib.core.project_model.ProjectConfig.credentials_scope]:
``"shared"`` (default) writes to the host-wide bucket every project
sees, ``"project"`` carves out a private vault row and agent-config
mount tree keyed by the project name.  Host-wide ``terok auth`` (no
``--project``) always writes to the shared bucket — there's no project
context to override it.
"""

from __future__ import annotations

import argparse
import sys

from ...lib.api import auth_image_staleness_warning, authenticate
from ...lib.core.images import require_agent_installed
from ...lib.core.projects import load_project, require_project_exists
from ._completers import complete_project_names as _complete_project_names, set_completer

# Display labels for the mode ids returned by ``available_auth_modes`` — the
# hyphenated forms read better in the listing than the internal identifiers.
_MODE_LABELS = {"oauth": "oauth", "device_auth": "device-code", "api_key": "api-key"}


def _provider_of() -> dict[str, str]:
    """Map each auth entry to the LLM provider it authenticates.

    Inverse of [`auth_provider_aliases`][terok.lib.domain.auth.auth_provider_aliases]
    (provider→entry); used to show both names in the auth listings.
    """
    from terok.lib.api.agents import auth_provider_aliases

    return {entry: provider for provider, entry in auth_provider_aliases().items()}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``auth`` top-level command."""
    from terok.lib.api.agents import AUTH_PROVIDERS

    # Accept either an auth-entry name (codex) or the LLM provider it
    # authenticates (openai → codex), since the two can be confusing.
    provider_of = _provider_of()
    accepted = list(AUTH_PROVIDERS) + list(provider_of.values())
    entries = []
    for name, p in AUTH_PROVIDERS.items():
        suffix = f" → {provider_of[name]}" if name in provider_of else ""
        entries.append(f"{name} ({p.label}{suffix})")
    providers_help = ", ".join(entries)
    p_auth = subparsers.add_parser(
        "auth",
        help="Authenticate an agent/tool (host-wide by default; --project scopes it)",
        description=(
            f"Available providers: {providers_help}\n\n"
            "Without arguments, opens an interactive menu to authenticate one "
            "or more providers in sequence.  ``terok auth <provider>`` "
            "authenticates host-wide — credentials are shared across every "
            "project that uses the same agent.  Pass ``--project <name>`` to "
            "scope the auth to a specific project: the project's image is "
            "reused, and if the project opted into per-project credentials "
            "(``credentials.scope: project`` in project.yml) the captured "
            "token lands in that project's private vault row instead of the "
            "shared bucket."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_auth.add_argument(
        "provider",
        nargs="?",
        default=None,
        choices=accepted,
        metavar="provider",
    )
    set_completer(
        p_auth.add_argument(
            "--project",
            dest="project_flag",
            default=None,
            help="Scope auth to a project (image + project.yml credentials.scope)",
        ),
        _complete_project_names,
    )
    p_auth.add_argument(
        "--device-auth",
        dest="device_auth",
        action="store_true",
        help=(
            "Force the headless device-code login (skip the method chooser) — "
            "for remote/headless hosts with no local browser"
        ),
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok auth``.  Returns True if handled."""
    if args.cmd != "auth":
        return False
    project_name = args.project_flag
    if args.provider is None:
        _run_interactive(project_name, device_auth=args.device_auth)
    else:
        _run_one(args.provider, project_name, device_auth=args.device_auth)
    return True


# ── Implementation helpers ────────────────────────────────────────────


def _run_one(provider: str, project_name: str | None, *, device_auth: bool = False) -> None:
    """Authenticate a single provider, optionally scoped to a project.

    *provider* may be an auth-entry name or an LLM-provider alias of one;
    ``authenticate`` resolves it, and the install check resolves it too so the
    baked-agent lookup uses the agent name, not the provider alias.

    With *device_auth* the method chooser is skipped and the provider's
    headless device-code login runs directly.
    """
    from terok.lib.api.agents import resolve_auth_provider

    if project_name is not None:
        # Project-scoped: verify the L2 image actually has the agent baked
        # in before launching.  Host-wide auth resolves the image in the
        # facade and does its own checks there.
        require_agent_installed(
            load_project(project_name), resolve_auth_provider(provider), noun="Provider"
        )
    # Heads-up before launching: a stale project image bakes outdated login
    # scripts (host-wide auth returns None here — not yet detectable).
    if (warning := auth_image_staleness_warning(project_name)) is not None:
        print(warning, file=sys.stderr)
    authenticate(provider, project_name, device_auth=device_auth)


def _run_interactive(project_name: str | None, *, device_auth: bool = False) -> None:
    """Interactively pick one or more providers and authenticate each in turn.

    *device_auth* forces the device-code login for every selected provider —
    the headless escape hatch when driving the menu on a remote host.
    """
    from terok.lib.api.agents import AUTH_PROVIDERS, authenticated_entries, available_auth_modes

    if project_name is not None:
        require_project_exists(project_name)

    provider_names = list(AUTH_PROVIDERS)
    provider_of = _provider_of()
    authed = authenticated_entries(project_name)
    print("Authenticate agents — pick one or more by number or name (agent or provider):")
    for i, name in enumerate(provider_names, 1):
        info = AUTH_PROVIDERS[name]
        modes = f"[{', '.join(_MODE_LABELS[m] for m in available_auth_modes(name))}]"
        label = f"{info.label} → {provider_of[name]}" if name in provider_of else info.label
        mark = "  ✓ authenticated" if name in (authed or ()) else ""
        print(f"  {i:>2}. {name:<12} {label:<22} {modes:<30}{mark}".rstrip())
    if authed is None:
        print(
            "  (vault locked — cannot tell which entries are already authenticated)",
            file=sys.stderr,
        )

    try:
        answer = input("\nChoice (numbers or names, comma-separated; empty = cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not answer:
        return

    selected = _parse_provider_selection(answer, provider_names)
    if not selected:
        print("Nothing selected.", file=sys.stderr)
        return

    for provider in selected:
        print(f"\n── {provider} ─────────────────────")
        _run_one(provider, project_name, device_auth=device_auth)


def _parse_provider_selection(raw: str, provider_names: list[str]) -> list[str]:
    """Parse a comma-separated pick-list into a de-duped ordered provider list.

    Accepts numeric indices (1-based, matching the displayed menu), auth-entry
    names, or the LLM-provider alias of one (``openai`` → ``codex``).  Unknown
    tokens are reported on stderr and skipped — partial success is preferable to
    aborting the whole menu interaction.
    """
    from terok.lib.api.agents import resolve_auth_provider

    selected: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        resolved: str | None = None
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(provider_names):
                resolved = provider_names[idx]
        elif (canonical := resolve_auth_provider(token)) in provider_names:
            resolved = canonical
        if resolved is None:
            print(f"  Skipped unknown provider: {token!r}", file=sys.stderr)
            continue
        if resolved not in seen:
            selected.append(resolved)
            seen.add(resolved)
    return selected
