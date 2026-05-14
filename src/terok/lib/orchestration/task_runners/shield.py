# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task shield (egress firewall) policy.

``_apply_shield_policy`` is the entry point every runner calls after a
container starts — it honours ``shield.drop_on_task_run`` on creation,
``shield.on_task_restart`` on restart, and always reapplies the
roster-driven auth-protect denies that survive ``shield down``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from terok.lib.integrations.sandbox import down as _shield_down_impl

from ...core.config import SHIELD_SECURITY_HINT, get_shield_bypass_firewall_no_protection

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig

_DESIRED_SHIELD_STATE_FILENAME = "shield_desired_state"
_VALID_SHIELD_STATES = frozenset({"up", "down", "down_all"})


def _read_desired_shield_state(task_dir: Path) -> str | None:
    """Read the persisted shield state from the task directory."""
    path = task_dir / _DESIRED_SHIELD_STATE_FILENAME
    if not path.is_file():
        return None
    value = path.read_text().strip()
    return value if value in _VALID_SHIELD_STATES else None


def _write_desired_shield_state(task_dir: Path, state: str) -> None:
    """Persist the desired shield state to the task directory."""
    (task_dir / _DESIRED_SHIELD_STATE_FILENAME).write_text(f"{state}\n")


def _restore_shield_state(cname: str, task_dir: Path) -> None:
    """Restore the persisted shield state on container restart (``retain`` policy)."""
    desired = _read_desired_shield_state(task_dir)
    if not desired or not desired.startswith("down"):
        return
    try:
        _shield_down_impl(cname, task_dir, allow_all=(desired == "down_all"))
    except Exception as exc:
        import warnings

        warnings.warn(f"shield restore: {exc}", stacklevel=2)


def _drop_shield_on_creation(cname: str, task_dir: Path) -> None:
    """Drop the shield after fresh container creation and persist the state."""
    try:
        _shield_down_impl(cname, task_dir)
        _write_desired_shield_state(task_dir, "down")
        audit_path = task_dir / "shield" / "audit.jsonl"
        print(f"Shield is down. Audit log: {audit_path}")
        print(SHIELD_SECURITY_HINT)
    except Exception as exc:
        import warnings

        warnings.warn(f"shield drop: {exc}", stacklevel=2)


def _collect_route_hosts(route: object) -> frozenset[str]:
    """Return the egress hosts derived from a single vault route.

    Inspects ``route.upstream`` and (when present) ``route.oauth_refresh
    ['token_url']``, returning the non-empty ``netloc`` parts.  Empty
    set when neither URL is declared or both fail to parse.
    """
    from urllib.parse import urlparse

    hosts: set[str] = set()
    for url in (
        getattr(route, "upstream", "") or "",
        (getattr(route, "oauth_refresh", None) or {}).get("token_url", "")
        if isinstance(getattr(route, "oauth_refresh", None), dict)
        else "",
    ):
        if url and (host := urlparse(url).netloc):
            hosts.add(host)
    return frozenset(hosts)


def _auth_protect_hosts() -> dict[str, frozenset[str]]:
    """Return ``{provider: {hosts to deny}}`` from the active roster.

    For every roster entry with a ``vault.upstream``, harvest the host
    portion of the upstream URL and the OAuth refresh ``token_url`` (if
    declared).  These are the egress endpoints an in-container ``/login``
    flow must never reach — blocking them keeps a compromised or
    socially-engineered agent from completing the OAuth handshake even
    when the shield is in ``down`` mode (terok-ai/terok#873).

    Routes with ``vault.shared_domain: true`` are skipped: their upstream
    apex also serves docs, dashboards, ``git push`` and similar non-API
    traffic, so a host-level deny would overshoot.  Credential containment
    (read-only shadow over the on-disk token) is the actual containment
    story for those providers.  See the field declaration on
    [`VaultRoute`][terok_executor.roster.types.VaultRoute] for the rationale.
    """
    from terok.lib.integrations.executor import get_roster

    out: dict[str, frozenset[str]] = {}
    for name, route in get_roster().vault_routes.items():
        if getattr(route, "shared_domain", False):
            continue
        if hosts := _collect_route_hosts(route):
            out[name] = hosts
    return out


def _resolved_allow_entries(shield_obj: Any) -> frozenset[str]:
    """Return the set of domain/IP entries in the active allow profiles.

    The set is the union of every line in every profile listed in
    ``shield.config.default_profiles``.  Used as the opt-out signal for
    auth-protect denies: a developer who needs direct access to an
    otherwise-blocked endpoint adds it to a custom allowlist profile (per
    [terok-ai/terok#566](https://github.com/terok-ai/terok/issues/566)).
    """
    try:
        names = list(shield_obj.config.default_profiles)
        if not names:
            return frozenset()
        return frozenset(shield_obj.profiles.compose_profiles(names))
    except Exception:  # noqa: BLE001
        return frozenset()


def _apply_auth_protect_denies(cname: str, task_dir: Path) -> None:
    """Deny agent OAuth/API endpoints in this task's container.

    Replaces the previous Anthropic/OpenAI special cases with a generic
    roster-driven loop.  The denies survive ``shield down`` (the deny set
    is repopulated on mode transitions), so an in-container ``/login``
    fails even when the egress firewall has been dropped for development.

    Skipped for providers in
    [`_exposed_credential_providers`][terok.lib.orchestration.environment._exposed_credential_providers]
    (where the writable credential file is intentional) and for hosts
    already present in the active allow profiles (the developer has
    explicitly opted that endpoint back in).
    """
    from terok.lib.integrations.sandbox import make_shield

    from ...core.config import exposed_credential_providers

    exposed = exposed_credential_providers()
    try:
        shield_obj = make_shield(task_dir)
    except Exception as exc:  # noqa: BLE001
        import warnings

        warnings.warn(f"auth-protect: shield unavailable: {exc}", stacklevel=2)
        return

    allow_entries = _resolved_allow_entries(shield_obj)

    for provider, hosts in _auth_protect_hosts().items():
        if provider in exposed:
            continue
        for host in hosts:
            if host in allow_entries:
                continue
            try:
                shield_obj.deny(cname, host)
            except Exception as exc:  # noqa: BLE001
                import warnings

                warnings.warn(f"auth-protect: deny {host}: {exc}", stacklevel=2)


def _apply_shield_policy(
    project: ProjectConfig, cname: str, task_dir: Path, *, is_restart: bool
) -> None:
    """Apply shield policy after container start (creation or restart).

    On fresh creation, honours ``shield.drop_on_task_run``.  On restart,
    honours ``shield.on_task_restart`` (``retain`` restores the last known
    state, ``up`` leaves the deny-all ruleset from the OCI hook).
    """
    if get_shield_bypass_firewall_no_protection():
        return

    if is_restart:
        policy = project.shield_on_task_restart
        if policy == "retain":
            _restore_shield_state(cname, task_dir)
        elif policy == "up":
            pass  # already UP from OCI hook
        else:
            raise ValueError(
                f"Unknown shield.on_task_restart value: {policy!r} (expected 'retain' or 'up')"
            )
    elif project.shield_drop_on_task_run:
        _drop_shield_on_creation(cname, task_dir)
    else:
        _write_desired_shield_state(task_dir, "up")

    _apply_auth_protect_denies(cname, task_dir)


__all__ = [
    "_apply_shield_policy",
]
