# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for NVIDIA CDI error detection via sandbox GpuConfigError.

The CDI hint logic lives in the podman backend of
``terok_sandbox.runtime``.  These tests verify that terok still surfaces
GPU errors correctly via the ``GpuConfigError`` / ``check_gpu_error``
path.
"""

import subprocess

import pytest
from terok_sandbox import GpuConfigError
from terok_sandbox.runtime.podman import check_gpu_error


def _make_error(stderr: str | bytes | None, returncode: int = 1) -> subprocess.CalledProcessError:
    """Create a ``CalledProcessError`` carrying test stderr content."""
    exc = subprocess.CalledProcessError(returncode, ["podman", "run"])
    exc.stderr = stderr if isinstance(stderr, bytes) or stderr is None else stderr.encode()
    return exc


@pytest.mark.parametrize(
    ("stderr", "expects_raise"),
    [
        ("Error: nvidia.com/gpu=all: device not found", True),
        ("Error: cdi.k8s.io: registry not configured", True),
        ("Error: CDI device injection failed", True),
        ("Error: image not found", False),
        ("Error: encoding failed", False),
        ("Error: cdi device failed", False),
        ("Error: Cdi device failed", False),
        ("", False),
        (None, False),
    ],
    ids=[
        "nvidia-device",
        "cdi-k8s",
        "uppercase-cdi",
        "unrelated",
        "lowercase-substring",
        "lowercase-cdi",
        "mixed-case-cdi",
        "empty",
        "none",
    ],
)
def test_cdi_hint_detection(stderr: str | None, expects_raise: bool) -> None:
    """CDI hint is raised only for explicit supported error patterns."""
    exc = _make_error(stderr)
    if expects_raise:
        with pytest.raises(GpuConfigError):
            check_gpu_error(exc)
    else:
        check_gpu_error(exc)  # should not raise


def test_gpu_config_error_contains_hint() -> None:
    """GpuConfigError carries the CDI hint for display purposes."""
    exc = _make_error("Error: nvidia.com/gpu=all: device not found")
    with pytest.raises(GpuConfigError) as exc_info:
        check_gpu_error(exc)
    assert "CDI" in exc_info.value.hint
    assert "podman-desktop.io" in exc_info.value.hint
