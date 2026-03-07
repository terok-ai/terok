# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""nft --check dry-run tests for generated rulesets."""

import shutil
import subprocess
import unittest

from terok.lib.security.shield.nft import hardened_ruleset, standard_ruleset


@unittest.skipIf(not shutil.which("nft"), "nft not installed")
class TestNftSyntaxValidation(unittest.TestCase):
    """Validate generated rulesets using nft --check."""

    def test_standard_ruleset_is_valid_nft(self) -> None:
        """Standard ruleset passes nft syntax check."""
        ruleset = standard_ruleset()
        result = subprocess.run(
            ["nft", "-c", "-f", "-"],
            input=ruleset,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, f"nft check failed: {result.stderr}")

    def test_hardened_ruleset_is_valid_nft(self) -> None:
        """Hardened ruleset passes nft syntax check."""
        ruleset = hardened_ruleset()
        result = subprocess.run(
            ["nft", "-c", "-f", "-"],
            input=ruleset,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, f"nft check failed: {result.stderr}")
