# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Hardened mode: named bridge + rootless-netns + optional dnsmasq.

Requires one-time setup: install nftables + dnsmasq, create bridge network.
Rules live in rootless-netns (outside containers).  Provides namespace
separation and optional dnsmasq --nftset integration.
"""

from __future__ import annotations

from ..shield.config import (
    BRIDGE_GATEWAY,
    BRIDGE_NETWORK,
    BRIDGE_SUBNET,
    NFT_TABLE_NAME,
    ensure_shield_dirs,
    get_shield_gate_port,
    shield_dns_dir,
    shield_resolved_dir,
)
from ..shield.dns import read_domains, resolve_and_write
from ..shield.nft import (
    add_elements,
    create_set,
    forward_rule,
    hardened_ruleset,
    safe_ip,
    safe_name,
)
from ..shield.profiles import profile_path
from ..shield.run import (
    nft_via_rootless_netns,
    podman_inspect,
)


def setup() -> None:
    """Verify bridge network exists and create directories."""
    ensure_shield_dirs()
    # Bridge network must be created manually (requires root the first time):
    #   podman network create --subnet 10.91.0.0/24 --gateway 10.91.0.1 ctr-egress


def _ensure_netns(gate_port: int | None = None) -> None:
    """Ensure rootless-netns has our nft table.  Idempotent."""
    if gate_port is None:
        gate_port = get_shield_gate_port()

    out = nft_via_rootless_netns("list", "table", "inet", "terok_shield", check=False)
    if "terok_shield" in out:
        return  # already loaded

    nft_via_rootless_netns(stdin=hardened_ruleset(BRIDGE_GATEWAY, BRIDGE_SUBNET, gate_port))

    # Verify
    out = nft_via_rootless_netns("list", "table", "inet", "terok_shield", check=False)
    if "terok_shield" not in out:
        raise RuntimeError("Failed to load nft table in rootless-netns")


def pre_start(
    profiles: list[str],
    container: str,
    dns_paths: list,
    gate_port: int | None = None,
) -> list[str]:
    """Prepare for container start.  Returns extra podman args.

    In hardened mode, the network is a named bridge instead of pasta/slirp.
    """
    if gate_port is None:
        gate_port = get_shield_gate_port()

    # Validate profiles
    for p in profiles:
        profile_path(p)

    _ensure_netns(gate_port)

    # Resolve DNS allowlists
    domains = read_domains(dns_paths, container)
    if domains:
        resolve_and_write(domains, container)

    return [
        "--network",
        BRIDGE_NETWORK,
        "--dns",
        BRIDGE_GATEWAY,
        "--cap-drop",
        "NET_ADMIN",
        "--cap-drop",
        "NET_RAW",
        "--security-opt",
        "no-new-privileges",
    ]


def post_start(profiles: list[str], container: str) -> None:
    """Call after podman run succeeds.  Creates per-container nft rules."""
    safe = safe_name(container)
    ip = podman_inspect(
        container,
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
    )

    # Create per-container set
    nft_via_rootless_netns(stdin=create_set(container))

    # Load profiles (rewrite set name to per-container)
    for p in profiles:
        pf = profile_path(p)
        content = pf.read_text().replace("allow_v4", f"{safe}_allow_v4")
        nft_via_rootless_netns(stdin=content)

    # Load pre-resolved IPs
    rf = shield_resolved_dir() / f"{container}.resolved"
    if rf.is_file():
        ips = [
            line.strip()
            for line in rf.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        cmd = add_elements(f"{safe}_allow_v4", ips)
        if cmd:
            nft_via_rootless_netns(stdin=cmd, check=False)

    # Add forward rule
    nft_via_rootless_netns(stdin=forward_rule(container, ip))

    # Optional: dnsmasq nftset integration
    _update_dnsmasq_nftsets(container, safe)


def pre_stop(container: str) -> None:
    """Call before podman stop.  Removes per-container nft rules."""
    safe = safe_name(container)

    # Remove forward rules by comment
    out = nft_via_rootless_netns(
        "-a",
        "list",
        "chain",
        "inet",
        "terok_shield",
        "forward",
        check=False,
    )
    for line in out.splitlines():
        if f"terok_shield:{safe}" in line and "handle" in line:
            parts = line.strip().split()
            try:
                h = parts[parts.index("handle") + 1]
                nft_via_rootless_netns(
                    "delete",
                    "rule",
                    "inet",
                    "terok_shield",
                    "forward",
                    "handle",
                    h,
                    check=False,
                )
            except (ValueError, IndexError):
                pass

    nft_via_rootless_netns(
        "delete",
        "set",
        "inet",
        "terok_shield",
        f"{safe}_allow_v4",
        check=False,
    )


def allow_ip(container: str, ip: str) -> None:
    """Live-allow an IP for a running container."""
    safe_ip(ip)
    safe = safe_name(container)
    nft_via_rootless_netns(
        "add",
        "element",
        "inet",
        "terok_shield",
        f"{safe}_allow_v4",
        f"{{ {ip} }}",
    )


def deny_ip(container: str, ip: str) -> None:
    """Live-deny an IP for a running container."""
    safe_ip(ip)
    safe = safe_name(container)
    nft_via_rootless_netns(
        "delete",
        "element",
        "inet",
        "terok_shield",
        f"{safe}_allow_v4",
        f"{{ {ip} }}",
    )


def list_rules(container: str) -> str:
    """List current nft rules for a container."""
    safe = safe_name(container)
    return nft_via_rootless_netns(
        "list",
        "set",
        "inet",
        "terok_shield",
        f"{safe}_allow_v4",
        check=False,
    )


def _update_dnsmasq_nftsets(container: str, safe: str) -> None:
    """Write dnsmasq nftset config for this container (best-effort)."""
    dns_file = shield_dns_dir() / f"{container}.txt"
    if not dns_file.is_file():
        return
    domains = [
        line.strip()
        for line in dns_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not domains:
        return
    lines = [f"nftset=/{d}/4#inet#{NFT_TABLE_NAME}#{safe}_allow_v4" for d in domains]
    nftset_file = shield_resolved_dir() / f"{container}.dnsmasq-nftset"
    nftset_file.write_text("\n".join(lines) + "\n")
