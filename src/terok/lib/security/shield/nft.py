# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""nftables ruleset generation.

+=====================================================+
|  SECURITY BOUNDARY -- read this file first.         |
|                                                     |
|  Every nftables ruleset is generated here.          |
|  All inputs are validated before interpolation.     |
|  RFC1918 blocks are structurally before allows.     |
|  Zero terok imports -- stdlib only.                 |
+=====================================================+
"""

from __future__ import annotations

import ipaddress
import re
import textwrap

# ── Constants (duplicated from config.py to maintain zero-import boundary) ──

NFT_TABLE = "inet terok_shield"
NFT_TABLE_NAME = "terok_shield"

RFC1918: tuple[str, ...] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
)

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")

# ── Validation ───────────────────────────────────────────


def safe_name(name: str) -> str:
    """Validate and normalize name for nft identifiers.

    Raises ValueError if the name contains unsafe characters.
    Hyphens are replaced with underscores for nft compatibility.
    """
    if not _SAFE_NAME.match(name):
        raise ValueError(f"Unsafe nft identifier: {name!r}")
    return name.replace("-", "_")


def safe_ip(value: str) -> str:
    """Validate IPv4 address or CIDR notation.

    Prevents nft command injection by ensuring the value is a valid
    IPv4 address or network.  Raises ValueError on invalid input.
    """
    v = value.strip()
    try:
        if "/" in v:
            ipaddress.IPv4Network(v, strict=False)
        else:
            ipaddress.IPv4Address(v)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as e:
        raise ValueError(f"Invalid IP/CIDR: {v!r}") from e
    return v


# ── Rulesets ─────────────────────────────────────────────


def _rfc1918_rules(prefix: str = "TEROK_SHIELD_RFC1918") -> str:
    """Generate RFC1918 reject rules.  Used by both modes."""
    return "\n".join(
        f'        ip daddr {net} log prefix "{prefix}: " reject with icmp type admin-prohibited'
        for net in RFC1918
    )


def _audit_deny_rule() -> str:
    """Generate the deny-all rule with audit logging."""
    return (
        '        log prefix "TEROK_SHIELD_DENIED: " counter\n'
        "        reject with icmp type admin-prohibited"
    )


def _audit_allow_rule() -> str:
    """Generate an audit rule for allowed traffic (rate-limited)."""
    return '        ip daddr @allow_v4 limit rate 10/second log prefix "TEROK_SHIELD_ALLOWED: " counter accept'


def standard_ruleset(dns: str = "169.254.0.1", gate_port: int = 9418) -> str:
    """Generate a per-container nftables ruleset for standard mode.

    Applied by OCI hook into the container's own netns.

    Chain order (output):
        loopback -> established -> DNS -> gate port -> RFC1918 block -> allow set -> deny

    Args:
        dns: DNS server address (pasta default forwarder).
        gate_port: Gate server port to allow on loopback.
    """
    safe_ip(dns)
    return textwrap.dedent(f"""\
        table {NFT_TABLE} {{
            set allow_v4 {{ type ipv4_addr; flags interval; }}

            chain output {{
                type filter hook output priority filter; policy drop;
                oifname "lo" accept
                ct state established,related accept
                udp dport 53 ip daddr {dns} accept
                tcp dport 53 ip daddr {dns} accept
                tcp dport {gate_port} oifname "lo" accept
        {_rfc1918_rules()}
        {_audit_allow_rule()}
                ip daddr @allow_v4 accept
        {_audit_deny_rule()}
            }}

            chain input {{
                type filter hook input priority filter; policy drop;
                iifname "lo" accept
                ct state established,related accept
                udp sport 53 accept
                tcp sport 53 accept
                drop
            }}
        }}
    """)


def hardened_ruleset(
    gw: str = "10.91.0.1",
    subnet: str = "10.91.0.0/24",
    gate_port: int = 9418,
) -> str:
    """Generate rootless-netns nftables ruleset for hardened mode.

    Applied to the forward chain (traffic crosses bridge).

    Chain order (forward):
        established -> ICMP -> DNS -> gate -> RFC1918 block -> intra-bridge -> allow set -> deny

    Args:
        gw: Bridge gateway address.
        subnet: Bridge subnet CIDR.
        gate_port: Gate server port to allow via gateway.
    """
    safe_ip(gw)
    safe_ip(subnet)
    return textwrap.dedent(f"""\
        table {NFT_TABLE} {{
            set global_allow_v4 {{ type ipv4_addr; flags interval; }}

            chain forward {{
                type filter hook forward priority filter; policy drop;
                ct state established,related accept
                ip protocol icmp accept
                ip daddr {gw} udp dport 53 accept
                ip daddr {gw} tcp dport 53 accept
                ip daddr {gw} tcp dport {gate_port} accept
        {_rfc1918_rules()}
                ip daddr {subnet} ip saddr {subnet} accept
                ip daddr @global_allow_v4 accept
        {_audit_deny_rule()}
            }}
        }}
    """)


# ── Set operations ───────────────────────────────────────


def add_elements(set_name: str, ips: list[str], table: str = NFT_TABLE) -> str:
    """Generate nft command to add validated IPs to a set.

    Returns empty string if no valid IPs.
    """
    valid = [safe_ip(ip) for ip in ips if _try_validate(ip)]
    if not valid:
        return ""
    return f"add element {table} {set_name} {{ {', '.join(valid)} }}\n"


def create_set(name: str, table: str = NFT_TABLE) -> str:
    """Generate nft command to create a per-container allow set."""
    n = safe_name(name)
    return f"add set {table} {n}_allow_v4 {{ type ipv4_addr; flags interval; }}\n"


def forward_rule(container: str, ip: str, table: str = NFT_TABLE) -> str:
    """Generate per-container forward rule for hardened mode."""
    n = safe_name(container)
    safe_ip(ip)
    return (
        f"add rule {table} forward ip saddr {ip} "
        f'ip daddr @{n}_allow_v4 accept comment "terok_shield:{n}"\n'
    )


# ── Verification ─────────────────────────────────────────


def verify_ruleset(nft_output: str) -> list[str]:
    """Check applied ruleset invariants.  Returns errors (empty = OK).

    Verifies:
    - Default policy is drop
    - Reject type is present
    - Deny log prefix is present
    - All RFC1918 ranges are blocked
    """
    checks = [
        ("policy drop" in nft_output, "policy is not drop"),
        ("admin-prohibited" in nft_output, "reject type missing"),
        ("TEROK_SHIELD_DENIED" in nft_output, "deny log prefix missing"),
    ] + [(net in nft_output, f"RFC1918 block for {net} missing") for net in RFC1918]
    return [msg for ok, msg in checks if not ok]


def _try_validate(ip: str) -> bool:
    """Return True if ip is a valid IPv4 address/CIDR, False otherwise."""
    try:
        safe_ip(ip)
        return True
    except ValueError:
        return False
