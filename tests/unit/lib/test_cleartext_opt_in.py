# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Plain HTTP stays on loopback unless the operator declares the network trusted."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.orchestration.tasks import task_new
from terok.lib.util.net import ALLOW_CLEARTEXT_ENV, is_loopback_host, require_cleartext_opt_in
from tests.test_utils import mock_git_config, project_env

_PROJECT = "cleartext"


@pytest.fixture(autouse=True)
def _no_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOW_CLEARTEXT_ENV, raising=False)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]"])
def test_loopback_needs_no_opt_in(host: str) -> None:
    assert is_loopback_host(host)
    require_cleartext_opt_in(host, what="A password")


def test_beyond_loopback_is_refused_and_names_both_remedies() -> None:
    with pytest.raises(SystemExit) as stop:
        require_cleartext_opt_in("lanbox", what="A password")
    message = str(stop.value)
    assert "A password" in message
    assert "TLS reverse proxy" in message
    assert ALLOW_CLEARTEXT_ENV in message


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_the_opt_in_allows_a_trusted_lan(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ALLOW_CLEARTEXT_ENV, value)
    require_cleartext_opt_in("lanbox", what="A password")


def test_toad_link_beyond_loopback_is_refused_before_any_port_is_taken(
    monkeypatch: pytest.MonkeyPatch, mock_runtime
) -> None:
    """A non-loopback public host stops the toad launch unless the network is declared trusted."""
    from terok.lib.orchestration.task_runners.toad import task_run_toad

    monkeypatch.setenv("TEROK_PUBLIC_HOST", "lanbox")
    toad = "terok.lib.orchestration.task_runners.toad"
    with project_env(f"project:\n  id: {_PROJECT}\n", project_name=_PROJECT), mock_git_config():
        task_id = task_new(_PROJECT)
        with (
            patch(f"{toad}.validate_host_setup"),
            patch(f"{toad}.load_task_meta", return_value=({}, Path("/tmp/terok-testing/meta.yml"))),
            patch(f"{toad}._refuse_existing_container"),
            patch(f"{toad}.assign_web_port") as ports,
            pytest.raises(SystemExit, match=ALLOW_CLEARTEXT_ENV),
        ):
            task_run_toad(_PROJECT, task_id)
    ports.assert_not_called()
