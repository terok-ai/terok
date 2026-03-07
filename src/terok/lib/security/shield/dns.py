# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Domain resolution for shield allowlists."""

from __future__ import annotations

from pathlib import Path

from .config import shield_dns_dir, shield_resolved_dir
from .run import dig


def read_domains(paths: list[Path], container: str | None = None) -> list[str]:
    """Read domains from base + per-container allowlists.

    Args:
        paths: List of domain list files to read.
        container: Optional container name for per-container overrides.
    """
    domains: list[str] = []
    all_paths = list(paths)

    # Add per-container override if it exists
    if container:
        per_ctr = shield_dns_dir() / f"{container}.txt"
        if per_ctr.is_file():
            all_paths.append(per_ctr)

    for f in all_paths:
        if not f.is_file():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                domains.append(line)
    return domains


def resolve_all(domains: list[str]) -> list[str]:
    """Resolve all domains to sorted unique IPv4 addresses."""
    ips: set[str] = set()
    for domain in domains:
        ips.update(dig(domain))
    return sorted(ips)


def resolve_and_write(domains: list[str], container: str) -> list[str]:
    """Resolve domains and write IPs to state file for hook consumption."""
    ips = resolve_all(domains)
    resolved_dir = shield_resolved_dir()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    resolved_file = resolved_dir / f"{container}.resolved"
    resolved_file.write_text("\n".join(ips) + "\n" if ips else "")
    return ips
