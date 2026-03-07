# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""terok-shield: container network firewall.

Public API for shield integration with the rest of terok.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .audit import list_log_files, log_event, tail_log
from .config import (
    ShieldConfig,
    ShieldMode,
    ensure_shield_dirs,
    get_shield_gate_port,
    load_project_shield_config,
    load_shield_config,
)
from .profiles import list_profiles
from .run import dig

if TYPE_CHECKING:
    from ...core.project_model import Project


def is_shield_active(project: Project) -> bool:
    """Return True if shield is active for the given project.

    Shield is active when:
    1. Global config has shield mode != disabled
    2. Per-project config has not opted out (enabled: false)
    """
    global_cfg = load_shield_config()
    if global_cfg.mode == ShieldMode.DISABLED:
        return False

    project_cfg = load_project_shield_config(project.root)
    return project_cfg.get("enabled", True)


def shield_pre_start(project: Project, cname: str) -> list[str]:
    """Prepare shield for container start.

    Returns extra podman args (including network args).
    Must be called instead of _podman_network_args when shield is active.
    """
    global_cfg = load_shield_config()
    project_cfg = load_project_shield_config(project.root)

    # Resolve profiles
    profiles = project_cfg.get("profiles", global_cfg.default_profiles)
    if not isinstance(profiles, list):
        profiles = global_cfg.default_profiles

    # Collect DNS allowlist paths
    dns_paths = _collect_dns_paths(project_cfg)

    gate_port = get_shield_gate_port()

    log_event(cname, "setup", detail=f"profiles={','.join(profiles)}")

    if global_cfg.mode == ShieldMode.HARDENED:
        from . import hardened

        args = hardened.pre_start(profiles, cname, dns_paths, gate_port)
    else:
        from . import standard

        args = standard.pre_start(profiles, cname, dns_paths, gate_port)

    return args


def shield_post_start(project: Project, cname: str) -> None:
    """Post-start hook.  Only needed for hardened mode."""
    global_cfg = load_shield_config()
    if global_cfg.mode != ShieldMode.HARDENED:
        return

    project_cfg = load_project_shield_config(project.root)
    profiles = project_cfg.get("profiles", global_cfg.default_profiles)
    if not isinstance(profiles, list):
        profiles = global_cfg.default_profiles

    from . import hardened

    hardened.post_start(profiles, cname)
    log_event(cname, "setup", detail="hardened post_start complete")


def shield_pre_stop(cname: str) -> None:
    """Pre-stop hook.  Only needed for hardened mode."""
    global_cfg = load_shield_config()
    if global_cfg.mode != ShieldMode.HARDENED:
        return

    from . import hardened

    hardened.pre_stop(cname)
    log_event(cname, "teardown")


def shield_allow_domain(cname: str, domain: str) -> list[str]:
    """Live-allow a domain for a running container.

    Returns the list of resolved IPs that were allowed.
    """
    global_cfg = load_shield_config()
    ips = dig(domain)

    if global_cfg.mode == ShieldMode.HARDENED:
        from . import hardened

        for ip in ips:
            hardened.allow_ip(cname, ip)
    else:
        from . import standard

        for ip in ips:
            standard.allow_ip(cname, ip)

    for ip in ips:
        log_event(cname, "allowed", dest=ip, detail=f"domain={domain}")

    return ips


def shield_deny_domain(cname: str, domain: str) -> list[str]:
    """Live-deny a domain for a running container.

    Returns the list of resolved IPs that were denied.
    """
    global_cfg = load_shield_config()
    ips = dig(domain)

    if global_cfg.mode == ShieldMode.HARDENED:
        from . import hardened

        for ip in ips:
            try:
                hardened.deny_ip(cname, ip)
            except Exception:
                pass
    else:
        from . import standard

        for ip in ips:
            try:
                standard.deny_ip(cname, ip)
            except Exception:
                pass

    for ip in ips:
        log_event(cname, "denied", dest=ip, detail=f"domain={domain}")

    return ips


def shield_status() -> dict:
    """Return current shield status information."""
    cfg = load_shield_config()
    return {
        "mode": cfg.mode.value,
        "profiles": list_profiles(),
        "audit_enabled": cfg.audit_enabled,
        "log_files": list_log_files(),
    }


def shield_setup(hardened: bool = False) -> None:
    """Run shield setup (install hook or verify bridge)."""
    ensure_shield_dirs()

    if hardened:
        from . import hardened as hw

        hw.setup()
    else:
        from . import standard as sw

        sw.setup()


def shield_rules(cname: str) -> str:
    """Return current nft rules for a container."""
    cfg = load_shield_config()
    if cfg.mode == ShieldMode.HARDENED:
        from . import hardened

        return hardened.list_rules(cname)
    else:
        from . import standard

        return standard.list_rules(cname)


def _collect_dns_paths(project_cfg: dict) -> list[Path]:
    """Collect DNS allowlist file paths from bundled + project config."""
    import importlib.resources

    paths: list[Path] = []

    # Bundled base DNS
    try:
        ref = importlib.resources.files("terok.resources.shield.dns").joinpath("base.txt")
        if ref.is_file():  # type: ignore[union-attr]
            paths.append(Path(str(ref)))
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        pass

    # Per-profile DNS files
    profiles = project_cfg.get("profiles", ["dev-standard"])
    if isinstance(profiles, list):
        for profile_name in profiles:
            try:
                ref = importlib.resources.files("terok.resources.shield.dns").joinpath(
                    f"{profile_name}.txt"
                )
                if ref.is_file():  # type: ignore[union-attr]
                    paths.append(Path(str(ref)))
            except (TypeError, FileNotFoundError, ModuleNotFoundError):
                pass

    # Extra domains from project config written as inline list
    allow_domains = project_cfg.get("allow_domains", [])
    if isinstance(allow_domains, list) and allow_domains:
        # Write to a temp file that read_domains can consume
        from .config import shield_dns_dir

        dns_dir = shield_dns_dir()
        dns_dir.mkdir(parents=True, exist_ok=True)
        extra_file = dns_dir / "_project_extra.txt"
        extra_file.write_text("\n".join(allow_domains) + "\n")
        paths.append(extra_file)

    return paths


__all__ = [
    "is_shield_active",
    "shield_pre_start",
    "shield_post_start",
    "shield_pre_stop",
    "shield_allow_domain",
    "shield_deny_domain",
    "shield_status",
    "shield_setup",
    "shield_rules",
    "ShieldConfig",
    "ShieldMode",
    "load_shield_config",
    "tail_log",
    "list_log_files",
    "log_event",
]
