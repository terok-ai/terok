# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The armed TUI panic button starts the full kill-switch, without a second dialog."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from terok.lib.api import execute_panic
from terok.tui.app import TerokTUI


@pytest.mark.asyncio
async def test_panic_worker_executes_the_whole_shutdown():
    """There is no separate stop phase to decline or accidentally skip."""
    stub = SimpleNamespace(run_worker=MagicMock())
    await TerokTUI._execute_panic(stub)
    assert stub.run_worker.call_args.args == (execute_panic,)
    assert stub.run_worker.call_args.kwargs["thread"] is True
