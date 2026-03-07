# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Subprocess helpers for shield.  Every external command goes through here."""

from __future__ import annotations

import re
import shutil
import subprocess

_IPV4 = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


class ExecError(Exception):
    """Raised when a subprocess fails."""

    def __init__(self, cmd: list[str], rc: int, stderr: str) -> None:
        """Store command details and format the error message."""
        self.cmd = cmd
        self.rc = rc
        self.stderr = stderr
        super().__init__(f"{cmd!r} failed (rc={rc}): {stderr.strip()}")


def run(cmd: list[str], *, check: bool = True, stdin: str | None = None) -> str:
    """Run a command, return stdout.  Raise ExecError on failure when check=True."""
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise ExecError(cmd, r.returncode, r.stderr or "")
    return r.stdout or ""


def has(name: str) -> bool:
    """Return True if an executable is on PATH."""
    return shutil.which(name) is not None


# ── nft wrappers ─────────────────────────────────────────


def nft(*args: str, stdin: str | None = None, check: bool = True) -> str:
    """Run nft command directly (standard mode, inside container netns)."""
    if stdin is not None:
        return run(["nft", "-f", "-"], stdin=stdin, check=check)
    return run(["nft", *args], check=check)


def nft_via_nsenter(container: str, *args: str, check: bool = True) -> str:
    """Run nft inside a running container's network namespace."""
    pid = podman_inspect(container, "{{.State.Pid}}")
    return run(
        ["podman", "unshare", "nsenter", "-t", pid, "-n", "nft", *args],
        check=check,
    )


def nft_via_rootless_netns(*args: str, stdin: str | None = None, check: bool = True) -> str:
    """Run nft inside podman's rootless-netns (hardened mode)."""
    cmd = ["podman", "unshare", "--rootless-netns", "nft"]
    if stdin is not None:
        cmd += ["-f", "-"]
        return run(cmd, stdin=stdin, check=check)
    return run([*cmd, *args], check=check)


# ── Other helpers ────────────────────────────────────────


def podman_inspect(container: str, fmt: str) -> str:
    """Inspect a container attribute via podman."""
    return run(["podman", "inspect", "--format", fmt, container]).strip()


def dig(domain: str) -> list[str]:
    """Resolve domain to a list of IPv4 addresses.  Empty on failure."""
    out = run(["dig", "+short", "A", domain], check=False)
    return [line.strip() for line in out.splitlines() if _IPV4.match(line.strip())]
