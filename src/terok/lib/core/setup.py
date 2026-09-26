# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terok's setup receipt and downward host-readiness checks."""

from importlib.metadata import version

from terok_util import (
    SetupCheck,
    SetupReceipt,
    require_no_downgrade,
    require_setup,
)

from terok.lib.integrations import executor

from .config import get_tui_desktop_entry, make_sandbox_config
from .paths import core_state_dir

_OWNER = "terok"
_RECEIPT = "setup.json"


def check_setup(*, live: bool = False) -> tuple[SetupCheck, ...]:
    """Check terok's setup and the executor-owned dependency closure."""
    return (_receipt().check(), *executor.check_setup(make_sandbox_config(), live=live))


def preflight_setup() -> None:
    """Reject downgrades throughout the closure before setup writes anything."""
    require_no_downgrade(check_setup())


def invalidate_setup() -> None:
    """Invalidate terok's receipt immediately before its setup writes."""
    _receipt().clear()


def complete_setup() -> None:
    """Certify successful terok work only after its dependencies are ready."""
    require_setup(executor.check_setup(make_sandbox_config()))
    _receipt().write()


def validate_host_setup() -> None:
    """Require live host readiness before starting or disturbing a task."""
    require_setup(check_setup(live=True))


def _receipt() -> SetupReceipt:
    """Describe only the version and setup inputs owned by terok."""
    return SetupReceipt(
        core_state_dir() / _RECEIPT,
        _OWNER,
        version(_OWNER),
        {"desktop_policy": get_tui_desktop_entry()},
    )
