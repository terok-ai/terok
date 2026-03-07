# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Standard mode: OCI hooks + per-container netns.

No root required.  No host packages beyond podman + nft.
The OCI hook (hook.py) applies nftables rules at container creation.
Live changes via nsenter into the container's network namespace.
"""

from __future__ import annotations

import os
import stat

from ...util.podman import _detect_rootless_network_mode
from ..shield.config import (
    ANNOTATION_KEY,
    ensure_shield_dirs,
    get_shield_gate_port,
    shield_hook_entrypoint,
    shield_hooks_dir,
)
from ..shield.dns import read_domains, resolve_and_write
from ..shield.hook import generate_entrypoint, generate_hook_json
from ..shield.nft import safe_ip
from ..shield.profiles import profile_path
from ..shield.run import nft_via_nsenter


def setup() -> None:
    """Install OCI hook JSON and entrypoint script."""
    ensure_shield_dirs()

    ep = shield_hook_entrypoint()
    ep.write_text(generate_entrypoint())
    ep.chmod(ep.stat().st_mode | stat.S_IEXEC)

    hooks_dir = shield_hooks_dir()
    (hooks_dir / "terok-shield-hook.json").write_text(generate_hook_json(ep))


def pre_start(
    profiles: list[str],
    container: str,
    dns_paths: list,
    gate_port: int | None = None,
) -> list[str]:
    """Prepare for container start.  Resolves DNS, writes state.

    Returns list of extra podman args to include in the run command,
    including network args (replaces _podman_network_args).
    """
    if gate_port is None:
        gate_port = get_shield_gate_port()

    # Validate profiles exist
    for p in profiles:
        profile_path(p)  # raises FileNotFoundError if missing

    # Resolve DNS allowlists
    domains = read_domains(dns_paths, container)
    if domains:
        resolve_and_write(domains, container)

    # Build podman args — includes network setup
    args: list[str] = []

    # Network args (same as _podman_network_args but with shield awareness)
    if os.geteuid() != 0:
        mode = _detect_rootless_network_mode()
        if mode == "slirp4netns":
            args += [
                "--network",
                "slirp4netns:allow_host_loopback=true",
                "--add-host",
                "host.containers.internal:10.0.2.2",
            ]
        elif mode == "pasta":
            args += [
                "--network",
                f"pasta:-T,{gate_port}",
                "--add-host",
                "host.containers.internal:127.0.0.1",
            ]

    # Shield-specific args
    args += [
        "--annotation",
        f"{ANNOTATION_KEY}={','.join(profiles)}",
        "--hooks-dir",
        str(shield_hooks_dir()),
        "--cap-drop",
        "NET_ADMIN",
        "--cap-drop",
        "NET_RAW",
        "--security-opt",
        "no-new-privileges",
    ]
    return args


def allow_ip(container: str, ip: str) -> None:
    """Live-allow an IP for a running container."""
    safe_ip(ip)
    nft_via_nsenter(
        container,
        "add",
        "element",
        "inet",
        "terok_shield",
        "allow_v4",
        f"{{ {ip} }}",
    )


def deny_ip(container: str, ip: str) -> None:
    """Live-deny an IP for a running container."""
    safe_ip(ip)
    nft_via_nsenter(
        container,
        "delete",
        "element",
        "inet",
        "terok_shield",
        "allow_v4",
        f"{{ {ip} }}",
    )


def list_rules(container: str) -> str:
    """List current nft rules for a container."""
    return nft_via_nsenter(container, "list", "table", "inet", "terok_shield", check=False)
