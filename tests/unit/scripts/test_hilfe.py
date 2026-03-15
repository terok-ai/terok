# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the in-container ``hilfe`` helper script."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HILFE_SCRIPT = REPO_ROOT / "src" / "terok" / "resources" / "scripts" / "hilfe"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _run_hilfe(*args: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    """Run the ``hilfe`` script and return the completed process."""
    env = {
        "LANG": "C.UTF-8",
        "PATH": os.defpath,
    }
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HILFE_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _plain(text: str) -> str:
    """Strip ANSI escape sequences from *text* for stable assertions."""
    return ANSI_RE.sub("", text)


def test_hilfe_kurz_keeps_banner_compact() -> None:
    """``hilfe --kurz`` keeps the login banner brief."""
    result = _run_hilfe("--kurz", TEROK_UNRESTRICTED="1", _TEROK_LOGIN="")
    text = _plain(result.stdout)

    assert result.returncode == 0
    assert "Permission mode: unrestricted" in text
    assert "Available AI agents:" in text
    assert "OpenCode with Helmholtz Blablador" in text
    assert "Run hilfe for more container tips." in text
    assert "Run hilfe for container tips." not in text
    assert "/workspace" not in text
    assert "update-all-the-things" not in text


def test_hilfe_kurz_login_flow_message() -> None:
    """``hilfe --kurz`` uses the shorter hint on login."""
    result = _run_hilfe("--kurz", _TEROK_LOGIN="1")
    text = _plain(result.stdout)

    assert result.returncode == 0
    assert "Run hilfe for container tips." in text
    assert "Run hilfe for more container tips." not in text


def test_hilfe_full_includes_container_notes() -> None:
    """Plain ``hilfe`` includes the fuller container notes."""
    result = _run_hilfe()
    text = _plain(result.stdout)

    assert result.returncode == 0
    assert "/workspace" in text
    assert "/home/dev" in text
    assert "update-all-the-things" in text
    assert "Rebuild from L0 with fresh agents" in text
    assert "Rebuild from L0 (no cache)" in text
    assert "new containers only" in text
    assert "^a" in text
    assert "^b" in text


def test_hilfe_invalid_arg_prints_usage() -> None:
    """Unknown arguments fail with a short usage line."""
    result = _run_hilfe("--wat")

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Usage: hilfe [--kurz]\n"
