# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for NVIDIA CDI error detection and user hint."""

import subprocess

import pytest

from terok.lib.containers.task_runners import _CDI_HINT, _enrich_run_error


def make_error(stderr: str | bytes | None, returncode: int = 1) -> subprocess.CalledProcessError:
    """Create a ``CalledProcessError`` carrying test stderr content."""
    exc = subprocess.CalledProcessError(returncode, ["podman", "run"])
    exc.stderr = stderr if isinstance(stderr, bytes) or stderr is None else stderr.encode()
    return exc


@pytest.mark.parametrize(
    ("stderr", "expects_hint"),
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
def test_cdi_hint_detection(stderr: str | None, expects_hint: bool) -> None:
    """CDI hint is emitted only for explicit supported error patterns."""
    message = _enrich_run_error("Run failed", make_error(stderr))
    assert (_CDI_HINT in message) is expects_hint
    if stderr:
        assert stderr.split(": ", 1)[-1] in message


def test_prefix_in_message() -> None:
    """The supplied prefix is always included in the enriched error message."""
    message = _enrich_run_error("Custom prefix", make_error("some error"))
    assert message.startswith("Custom prefix:")
