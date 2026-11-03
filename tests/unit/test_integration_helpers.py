# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration fixtures satisfy runtime contracts without requiring real containers."""

import runpy
import subprocess
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from terok_executor import require_image_input_protocol, require_task_input_protocol
from terok_sandbox.runtime import podman

from tests.integration import helpers
from tests.testfs import CURRENT_CONTAINER_PROTOCOL, CURRENT_INPUT_LABELS, WORKSPACE_ROOT

CONTAINER_NAME = f"{helpers.PODMAN_CONTAINER_PREFIX}-diagnostics"
HOOK_ERROR = "terok-shield hook: missing nsenter"
PODMAN_ERROR = "crun: error executing hook"


@pytest.fixture
def fake_podman(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Execute only the generated Python shim in-process, never a Podman subprocess."""
    script = helpers.write_fake_podman(tmp_path, tmp_path / "runtime.json")

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Adapt the shim's output and exit status to the runtime's process interface."""
        output, error = StringIO(), StringIO()
        with (
            patch.object(sys, "argv", [str(script), *argv[1:]]),
            redirect_stdout(output),
            redirect_stderr(error),
            pytest.raises(SystemExit) as exited,
        ):
            runpy.run_path(str(script), run_name="__main__")
        return subprocess.CompletedProcess(
            argv, exited.value.code, output.getvalue(), error.getvalue()
        )

    monkeypatch.setattr(podman, "require_host_tool", lambda _name: str(script))
    monkeypatch.setattr(podman.subprocess, "run", run)
    return run


def test_fake_image_satisfies_strict_inspection(
    fake_podman: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """The fixture's labels and full image identity pass the production protocol preflight."""
    image = podman.PodmanRuntime().image(helpers.PODMAN_TEST_IMAGE)

    observed = image.inspect()

    assert observed is not None
    assert observed.labels == CURRENT_INPUT_LABELS
    require_image_input_protocol(image)
    by_id = podman.PodmanRuntime().image(observed.id).inspect()
    assert by_id == observed


def test_fake_container_satisfies_strict_inspection(
    fake_podman: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    """Creation inputs and subsequent lifecycle changes survive strict snapshot inspection."""
    workspace = tmp_path / "workspace"
    created = fake_podman(
        [
            "podman",
            "run",
            "--name",
            CONTAINER_NAME,
            "-e",
            f"TEROK_CONTAINER_PROTOCOL={CURRENT_CONTAINER_PROTOCOL}",
            "-v",
            f"{workspace}:{WORKSPACE_ROOT}:rw",
            helpers.PODMAN_TEST_IMAGE,
        ]
    )
    container = podman.PodmanRuntime().container(CONTAINER_NAME)

    observed = container.inspect()

    assert observed is not None
    assert observed.id == created.stdout.strip()
    assert observed.state == "running"
    assert observed.env == {"TEROK_CONTAINER_PROTOCOL": CURRENT_CONTAINER_PROTOCOL}
    assert observed.mounts == ((str(workspace), str(WORKSPACE_ROOT)),)
    require_task_input_protocol(container, inspection=observed)
    for operation, state in (("stop", "exited"), ("start", "running")):
        fake_podman(["podman", operation, CONTAINER_NAME])
        updated = container.inspect()
        assert updated is not None
        assert updated.state == state
        assert updated.id == observed.id
        assert updated.env == observed.env
        assert updated.mounts == observed.mounts


def test_fake_absent_container_is_confirmed_by_exists(
    fake_podman: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Missing inspection returns absence only through a failing existence probe."""
    assert podman.PodmanRuntime().container(CONTAINER_NAME).inspect() is None


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
