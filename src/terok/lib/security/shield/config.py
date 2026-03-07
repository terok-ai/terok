# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Shield configuration, constants, and path helpers."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

from ...core.config import get_gate_server_port, get_global_section, state_root

# ── Network constants ────────────────────────────────────

BRIDGE_NETWORK = "ctr-egress"
BRIDGE_SUBNET = "10.91.0.0/24"
BRIDGE_GATEWAY = "10.91.0.1"
PASTA_DNS = "169.254.0.1"

NFT_TABLE = "inet terok_shield"
NFT_TABLE_NAME = "terok_shield"

# Containers must never reach these.  Order is load-bearing
# (matches nftables chain evaluation order).
RFC1918: tuple[str, ...] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
)

ANNOTATION_KEY = "terok.shield.profiles"


class ShieldMode(enum.Enum):
    """Operating mode for the shield firewall."""

    DISABLED = "disabled"
    STANDARD = "standard"
    HARDENED = "hardened"


@dataclass(frozen=True)
class ShieldConfig:
    """Resolved shield configuration."""

    mode: ShieldMode = ShieldMode.DISABLED
    default_profiles: list[str] = field(default_factory=lambda: ["dev-standard"])
    audit_enabled: bool = True
    audit_log_allowed: bool = True


# ── Path helpers ─────────────────────────────────────────


def shield_state_dir() -> Path:
    """Return the shield state directory under terok's state root."""
    return state_root() / "shield"


def shield_hooks_dir() -> Path:
    """Return the OCI hooks directory."""
    return shield_state_dir() / "hooks"


def shield_hook_entrypoint() -> Path:
    """Return the path to the hook entrypoint script."""
    return shield_state_dir() / "terok-shield-hook"


def shield_profiles_dir() -> Path:
    """Return the user profiles directory (overrides bundled)."""
    return shield_state_dir() / "profiles"


def shield_logs_dir() -> Path:
    """Return the audit logs directory."""
    return shield_state_dir() / "logs"


def shield_dns_dir() -> Path:
    """Return the DNS allowlists directory."""
    return shield_state_dir() / "dns"


def shield_resolved_dir() -> Path:
    """Return the directory for pre-resolved IP files."""
    return shield_state_dir() / "resolved"


def ensure_shield_dirs() -> None:
    """Create all shield state directories."""
    for d in (
        shield_state_dir(),
        shield_hooks_dir(),
        shield_profiles_dir(),
        shield_logs_dir(),
        shield_dns_dir(),
        shield_resolved_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


# ── Config loading ───────────────────────────────────────


def load_shield_config() -> ShieldConfig:
    """Load shield configuration from global config."""
    section = get_global_section("shield")
    if not section:
        return ShieldConfig()

    mode_str = section.get("mode", "disabled")
    try:
        mode = ShieldMode(mode_str)
    except ValueError:
        if mode_str == "auto":
            mode = _auto_detect_mode()
        else:
            mode = ShieldMode.DISABLED

    profiles = section.get("default_profiles", ["dev-standard"])
    if not isinstance(profiles, list):
        profiles = ["dev-standard"]

    audit = section.get("audit", {})
    if not isinstance(audit, dict):
        audit = {}

    return ShieldConfig(
        mode=mode,
        default_profiles=profiles,
        audit_enabled=audit.get("enabled", True),
        audit_log_allowed=audit.get("log_allowed", True),
    )


def load_project_shield_config(project_root: Path) -> dict:
    """Load per-project shield overrides from project.yml."""
    import yaml

    project_yml = project_root / "project.yml"
    if not project_yml.is_file():
        return {}
    try:
        cfg = yaml.safe_load(project_yml.read_text()) or {}
        shield = cfg.get("shield", {})
        return shield if isinstance(shield, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def get_shield_gate_port() -> int:
    """Return the gate server port for shield rules."""
    return get_gate_server_port()


def _auto_detect_mode() -> ShieldMode:
    """Auto-detect the best available shield mode."""
    import shutil
    import subprocess

    # Check for hardened mode prerequisites (bridge network)
    try:
        subprocess.run(
            ["podman", "network", "exists", BRIDGE_NETWORK],
            check=True,
            capture_output=True,
        )
        if shutil.which("dnsmasq"):
            return ShieldMode.HARDENED
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Standard mode requires OCI hook support (nft binary)
    if shutil.which("nft"):
        return ShieldMode.STANDARD

    return ShieldMode.DISABLED
