# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Curated egress allowlist sets — named t40 content a project opts into.

Named bundles of well-known development endpoints a task can be granted at
MID granularity: coarse enough that a user picks a handful of named sets
instead of authoring host lists, fine enough that a Python-only project
doesn't drag in container registries.  The selection feeds the authored
t40 project-allow tier (ordinary allows — a security-deny always wins),
alongside the project's git remote host and its custom ``shield.allow``.

Ownership per the layering: terok owns the curated *workflow* content
here; the ``os-packages`` set alone resolves through terok-executor's
``package_repo_hosts`` — which distro repos a task needs is image
knowledge, keyed on the project's detected package family.

No curated set applies unless the project selects it via ``shield.sets``
in ``project.yml`` (the TUI chooser or ``terok shield sets`` write it).
``recommended`` names every set in the registry, including sets added to
it later, and the new-project wizard offers it.
"""

from __future__ import annotations

from collections.abc import Iterable

OS_PACKAGES_SET = "os-packages"
"""The one dynamic set: distro package repos, resolved by package family."""

OS_PACKAGES_SUMMARY = "distro repos, resolved by the image's package family"
"""What to show operators in place of a host list for the dynamic set."""

EGRESS_SETS: dict[str, tuple[str, ...]] = {
    "git-hosting": (
        "github.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
        "raw.githubusercontent.com",
        "gist.github.com",
        "gitlab.com",
        "bitbucket.org",
        "codeberg.org",
    ),
    "python": (
        "pypi.org",
        "files.pythonhosted.org",
    ),
    "node": (
        "registry.npmjs.org",
        "registry.yarnpkg.com",
        "nodejs.org",
    ),
    "rust": (
        "crates.io",
        "static.crates.io",
        "index.crates.io",
        "static.rust-lang.org",
    ),
    "go": (
        "proxy.golang.org",
        "sum.golang.org",
        "index.golang.org",
    ),
    "containers": (
        "registry-1.docker.io",
        "auth.docker.io",
        "index.docker.io",
        "production.cloudflare.docker.com",
        "quay.io",
        "cdn.quay.io",
        "cdn01.quay.io",
        "cdn02.quay.io",
        "cdn03.quay.io",
        "ghcr.io",
        "pkg-containers.githubusercontent.com",
        "registry.fedoraproject.org",
    ),
    OS_PACKAGES_SET: (),  # resolved dynamically — see resolve_egress_sets
}
"""Registry of curated sets: name → static hosts (``os-packages`` is dynamic)."""

RECOMMENDED_SET = "recommended"
"""The meta-set naming every set in ``EGRESS_SETS``, including sets added to it later.

It shares one flat namespace with the concrete set names: ``shield.sets``
lists ``recommended`` the same way it lists ``python``.
"""


def selected_egress_sets(names: tuple[str, ...] | None) -> tuple[str, ...]:
    """The concrete sets a ``shield.sets`` value grants; ``None`` (unset) grants none.

    [`RECOMMENDED_SET`][terok.lib.core.egress_sets.RECOMMENDED_SET] expands
    to every set in the registry; a set named twice counts once, at its
    first position.
    """
    chosen: list[str] = []
    for name in names or ():
        chosen += tuple(EGRESS_SETS) if name == RECOMMENDED_SET else (name,)
    return tuple(dict.fromkeys(chosen))


def describe_egress_sets(names: tuple[str, ...] | None) -> str:
    """Render a ``shield.sets`` value as authored — one wording, every surface."""
    return ", ".join(names or ()) or "none (no curated sets)"


def validate_egress_sets(names: Iterable[str] | None) -> None:
    """Reject unknown set names with the available names spelled out.

    Called at project-load time so a typo in ``shield.sets`` fails the
    load loudly instead of silently granting nothing.
    """
    if names is None:
        return
    available = (*EGRESS_SETS, RECOMMENDED_SET)
    unknown = [n for n in names if n not in available]
    if unknown:
        raise SystemExit(
            f"Unknown shield.sets entr{'ies' if len(unknown) > 1 else 'y'}: "
            f"{', '.join(map(repr, unknown))}.  Available sets: {', '.join(available)}"
        )


def resolve_egress_sets(names: tuple[str, ...] | None, family: str | None) -> tuple[str, ...]:
    """Resolve a set selection into its hosts (order-preserving, de-duplicated).

    *names* is the project's ``shield.sets``;
    [`selected_egress_sets`][terok.lib.core.egress_sets.selected_egress_sets]
    decides which sets it grants, so ``None`` and an empty tuple resolve to
    nothing and ``recommended`` resolves every set.  *family* is the project
    image's package family (``deb``/``rpm``/None), consumed by the
    ``os-packages`` set through terok-executor's ``package_repo_hosts``; an
    unrecognized image gets the all-family union.
    """
    from terok.lib.integrations.executor import package_repo_hosts

    hosts: list[str] = []
    for name in selected_egress_sets(names):
        hosts += package_repo_hosts(family) if name == OS_PACKAGES_SET else EGRESS_SETS[name]
    return tuple(dict.fromkeys(hosts))
