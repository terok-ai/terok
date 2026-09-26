# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield startup failures expose hook logs without requiring real containers."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tests.integration import helpers

CONTAINER_NAME = f"{helpers.PODMAN_CONTAINER_PREFIX}-diagnostics"
HOOK_ERROR = "terok-shield hook: missing nsenter"
PODMAN_ERROR = "crun: error executing hook"


@pytest.fixture
def podman_run(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace Podman with a failed process result for diagnostic tests."""
    run = Mock(return_value=subprocess.CompletedProcess([], 126, "podman output", PODMAN_ERROR))
    monkeypatch.setattr(helpers.subprocess, "run", run)
    return run


@pytest.fixture
def shield_args(tmp_path: Path) -> list[str]:
    """Use an isolated state directory with the normal global-hook arguments."""
    return ["--annotation", f"{helpers.SHIELD_STATE_DIR_ANNOTATION}={tmp_path}"]


@pytest.mark.parametrize("contents", [HOOK_ERROR.encode(), b"\xff\n\x1b[31m" + HOOK_ERROR.encode()])
def test_start_failure_reports_global_hook_log(
    tmp_path: Path, shield_args: list[str], podman_run: Mock, contents: bytes
) -> None:
    """A global hook's log is available even without an explicit hooks directory."""
    log_path = tmp_path / helpers.SHIELD_HOOK_ERROR_LOG
    log_path.write_bytes(contents)

    with pytest.raises(RuntimeError, match=r"podman run failed \(exit 126\)") as error:
        helpers.start_shielded_container(CONTAINER_NAME, shield_args)

    message = str(error.value)
    assert str(log_path) in message
    assert HOOK_ERROR in message
    assert PODMAN_ERROR in message
    assert "podman output" in message
    assert "--hooks-dir missing" not in message
    assert "\x1b" not in message
    podman_run.assert_called_once()


@pytest.mark.parametrize(
    "read_error", [FileNotFoundError("missing"), PermissionError("unreadable")]
)
def test_unavailable_hook_log_preserves_start_failure(
    tmp_path: Path, shield_args: list[str], podman_run: Mock, read_error: OSError
) -> None:
    """Unavailable diagnostics must not hide the original container-start error."""
    with (
        patch.object(Path, "read_text", side_effect=read_error),
        pytest.raises(RuntimeError, match=r"podman run failed \(exit 126\)") as error,
    ):
        helpers.start_shielded_container(CONTAINER_NAME, shield_args)

    message = str(error.value)
    assert PODMAN_ERROR in message
    assert str(tmp_path / helpers.SHIELD_HOOK_ERROR_LOG) in message
    assert str(read_error) in message
    podman_run.assert_called_once()


@pytest.mark.parametrize(
    "extra_args", [[], ["--annotation", f"{helpers.SHIELD_STATE_DIR_ANNOTATION}="]]
)
def test_missing_state_annotation_does_not_read_files(
    extra_args: list[str], podman_run: Mock
) -> None:
    """Missing hook state cannot fall back to an unrelated directory."""
    with (
        patch.object(Path, "read_text") as read,
        pytest.raises(RuntimeError, match=r"podman run failed \(exit 126\)") as error,
    ):
        helpers.start_shielded_container(CONTAINER_NAME, extra_args)

    assert f"missing {helpers.SHIELD_STATE_DIR_ANNOTATION} annotation" in str(error.value)
    read.assert_not_called()
    podman_run.assert_called_once()


def test_successful_start_does_not_read_hook_log(shield_args: list[str], podman_run: Mock) -> None:
    """A successful start needs no hook diagnostics."""
    podman_run.return_value.returncode = 0

    with patch.object(Path, "read_text") as read:
        helpers.start_shielded_container(CONTAINER_NAME, shield_args)

    read.assert_not_called()
    podman_run.assert_called_once()
