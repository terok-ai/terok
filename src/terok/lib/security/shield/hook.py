# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""OCI hook for standard mode.  Called by container runtime at createContainer.

Context: inside container netns, CAP_NET_ADMIN available, host filesystem,
untrusted workload NOT yet started.  Non-zero exit -> container torn down.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import (
    ANNOTATION_KEY,
    get_shield_gate_port,
    shield_resolved_dir,
)
from .nft import add_elements, standard_ruleset, verify_ruleset
from .profiles import profile_path
from .run import ExecError, nft


def hook_main() -> None:
    """Apply firewall rules.  Succeeds or raises SystemExit (fail-closed)."""
    # Parse OCI state from stdin
    try:
        state = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        _die(f"cannot parse container state: {e}")

    annotations = state.get("annotations", {})
    profiles_str = annotations.get(ANNOTATION_KEY, "")
    ctr_id = state.get("id", "unknown")[:12]

    if not profiles_str:
        _die(f"{ANNOTATION_KEY} annotation is empty")

    profiles = [p.strip() for p in profiles_str.split(",") if p.strip()]

    # 1. Load base ruleset
    gate_port = get_shield_gate_port()
    try:
        nft(stdin=standard_ruleset(gate_port=gate_port))
    except ExecError as e:
        _die(f"base ruleset: {e}")

    # 2. Load profiles
    for p in profiles:
        try:
            pf = profile_path(p)
        except FileNotFoundError:
            _die(f"profile '{p}' not found")
        try:
            nft("-f", str(pf))
        except ExecError as e:
            _die(f"profile '{p}': {e}")

    # 3. Load pre-resolved IPs
    rf = shield_resolved_dir() / f"{ctr_id}.resolved"
    if rf.is_file():
        ips = [
            line.strip()
            for line in rf.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        cmd = add_elements("allow_v4", ips)
        if cmd:
            nft(stdin=cmd, check=False)  # non-fatal: may contain stale IPs

    # 4. Self-verify (fail-closed guarantee)
    try:
        out = nft("list", "chain", "inet", "terok_shield", "output")
    except ExecError as e:
        _die(f"cannot verify: {e}")

    errors = verify_ruleset(out)
    if errors:
        for err in errors:
            print(f"VERIFY FAIL: {err}", file=sys.stderr)
        _die(f"verification failed ({len(errors)} errors)")


def generate_entrypoint() -> str:
    """Generate the Python script that OCI runtime calls as hook executable."""
    return (
        "#!/usr/bin/env python3\n"
        "# terok-shield OCI hook entrypoint (auto-generated)\n"
        "from terok.lib.security.shield.hook import hook_main\n"
        "hook_main()\n"
    )


def generate_hook_json(entrypoint: Path) -> str:
    """Generate the OCI hook JSON descriptor."""
    return json.dumps(
        {
            "version": "1.0.0",
            "hook": {"path": str(entrypoint.resolve())},
            "when": {"annotations": {f"^{ANNOTATION_KEY.replace('.', '\\\\.')}$": ".+"}},
            "stages": ["createContainer"],
        },
        indent=2,
    )


def _die(msg: str) -> None:
    """Exit with a fatal error message (fail-closed)."""
    raise SystemExit(f"FATAL: {msg}")
