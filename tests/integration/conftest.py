# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for shield integration tests.

Overrides the root autouse ``_mock_shield_pre_start`` so the real
``terok_shield`` library is exercised, and provides isolated shield
environments via environment-variable redirection.
"""

import json
import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from terok_shield import ShieldConfig, ShieldMode

from constants import GATE_PORT, TEST_IP

# ── Skip decorators ────────────────────────────────────────

skip_if_no_podman = pytest.mark.skipif(shutil.which("podman") is None, reason="podman not found")
skip_if_no_root = pytest.mark.skipif(os.geteuid() != 0, reason="root required")


# ── Autouse override ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_shield_pre_start() -> Iterator[None]:
    """Override root conftest: let real shield_pre_start execute."""
    yield


# ── Isolated shield environment ───────────────────────────


@pytest.fixture()
def shield_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect all shield state/config to a temp directory.

    Sets ``TEROK_SHIELD_STATE_DIR`` and ``TEROK_SHIELD_CONFIG_DIR`` so
    the real shield library writes hooks, logs, and caches into an
    isolated tree.  Returns a dict of key paths.
    """
    state = tmp_path / "state"
    config = tmp_path / "config"
    for sub in (
        state / "hooks",
        state / "logs",
        state / "dns",
        state / "resolved",
        config / "profiles",
    ):
        sub.mkdir(parents=True)
    monkeypatch.setenv("TEROK_SHIELD_STATE_DIR", str(state))
    monkeypatch.setenv("TEROK_SHIELD_CONFIG_DIR", str(config))
    return {
        "state": state,
        "config": config,
        "hooks": state / "hooks",
        "logs": state / "logs",
        "resolved": state / "resolved",
    }


# ── Standard test config ─────────────────────────────────


@pytest.fixture()
def shield_config() -> ShieldConfig:
    """Standard ShieldConfig for integration tests."""
    return ShieldConfig(
        mode=ShieldMode.HOOK,
        default_profiles=("dev-standard",),
        loopback_ports=(GATE_PORT,),
        audit_enabled=True,
        audit_log_allowed=True,
    )


# ── Installed hooks ───────────────────────────────────────


@pytest.fixture()
def installed_hooks(shield_env: dict[str, Path]) -> dict[str, Path]:
    """Shield environment with OCI hooks already installed."""
    from terok_shield.mode_hook import install_hooks

    install_hooks()
    return shield_env


# ── Mock factory for terok_shield.run.run ─────────────────


def mock_run_factory(rootless_mode: str = "pasta") -> Callable[..., str]:
    """Return a fake ``terok_shield.run.run`` that handles known commands.

    Handles ``podman info``, ``dig``, and silently ignores nft/inspect calls.
    """

    def mock_run(cmd: list[str], *, check: bool = True, stdin: str | None = None) -> str:
        if cmd[:2] == ["podman", "info"]:
            return json.dumps({"host": {"rootlessNetworkCmd": rootless_mode}})
        if cmd[0] == "dig":
            return f"{TEST_IP}\n"
        if cmd[0] == "nft" or cmd[:2] == ["podman", "inspect"]:
            return ""
        raise AssertionError(f"Unexpected terok_shield.run.run call: {cmd!r}")

    return mock_run
