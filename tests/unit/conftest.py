# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit-test fixtures.

Auto-mocks sandbox, shield, and credential proxy helpers so existing tests
do not require a real OCI hook, nftables, podman, proxy daemon, or root
privileges.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_infrastructure() -> Iterator[None]:
    """Replace Sandbox.run, shield down, and credential proxy with no-ops."""
    with (
        patch(
            "terok.lib.orchestration.task_runners._sandbox",
        ),
        patch(
            "terok.lib.orchestration.task_runners._shield_down_impl",
        ),
        patch(
            "terok.lib.core.config.get_credential_proxy_bypass",
            return_value=True,
        ),
    ):
        yield
