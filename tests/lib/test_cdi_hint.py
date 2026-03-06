# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for NVIDIA CDI error detection and user hint."""

import subprocess
import unittest

from terok.lib.containers.task_runners import _CDI_HINT, _enrich_run_error


class CdiHintTests(unittest.TestCase):
    """Tests for _enrich_run_error CDI detection."""

    def _make_error(self, stderr: str, returncode: int = 1) -> subprocess.CalledProcessError:
        """Create a CalledProcessError with stderr bytes."""
        exc = subprocess.CalledProcessError(returncode, ["podman", "run"])
        exc.stderr = stderr.encode()
        return exc

    def test_cdi_hint_on_nvidia_device_error(self) -> None:
        """CDI hint is shown when stderr mentions nvidia.com/gpu."""
        exc = self._make_error("Error: nvidia.com/gpu=all: device not found")
        msg = _enrich_run_error("Run failed", exc)
        self.assertIn(_CDI_HINT, msg)
        self.assertIn("nvidia.com/gpu=all", msg)

    def test_cdi_hint_on_cdi_k8s_error(self) -> None:
        """CDI hint is shown when stderr mentions cdi.k8s.io."""
        exc = self._make_error("Error: cdi.k8s.io: registry not configured")
        msg = _enrich_run_error("Run failed", exc)
        self.assertIn(_CDI_HINT, msg)

    def test_cdi_hint_on_uppercase_cdi_error(self) -> None:
        """CDI hint is shown when stderr mentions uppercase CDI."""
        exc = self._make_error("Error: CDI device injection failed")
        msg = _enrich_run_error("Run failed", exc)
        self.assertIn(_CDI_HINT, msg)

    def test_no_cdi_hint_on_unrelated_error(self) -> None:
        """CDI hint is NOT shown for unrelated errors."""
        exc = self._make_error("Error: image not found")
        msg = _enrich_run_error("Run failed", exc)
        self.assertNotIn(_CDI_HINT, msg)
        self.assertIn("image not found", msg)

    def test_no_cdi_hint_on_lowercase_cdi_substring(self) -> None:
        """CDI hint is NOT shown when stderr only contains 'cdi' as a lowercase substring."""
        exc = self._make_error("Error: encoding failed")
        msg = _enrich_run_error("Run failed", exc)
        self.assertNotIn(_CDI_HINT, msg)

    def test_empty_stderr_no_hint(self) -> None:
        """No CDI hint when stderr is empty."""
        exc = subprocess.CalledProcessError(1, ["podman", "run"])
        exc.stderr = b""
        msg = _enrich_run_error("Run failed", exc)
        self.assertNotIn(_CDI_HINT, msg)

    def test_none_stderr_no_hint(self) -> None:
        """No CDI hint when stderr is None."""
        exc = subprocess.CalledProcessError(1, ["podman", "run"])
        exc.stderr = None
        msg = _enrich_run_error("Run failed", exc)
        self.assertNotIn(_CDI_HINT, msg)

    def test_cdi_hint_only_on_explicit_patterns(self) -> None:
        """CDI hint is shown only for explicit patterns, not arbitrary case variants."""
        for text in ("Error: cdi device failed", "Error: Cdi device failed"):
            exc = self._make_error(text)
            msg = _enrich_run_error("Run failed", exc)
            self.assertNotIn(_CDI_HINT, msg, f"CDI hint should not match: {text!r}")
        exc = self._make_error("Error: CDI device failed")
        msg = _enrich_run_error("Run failed", exc)
        self.assertIn(_CDI_HINT, msg)

    def test_prefix_in_message(self) -> None:
        """The prefix is always included in the error message."""
        exc = self._make_error("some error")
        msg = _enrich_run_error("Custom prefix", exc)
        self.assertTrue(msg.startswith("Custom prefix:"))
