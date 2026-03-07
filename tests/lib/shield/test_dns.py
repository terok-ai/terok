# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for DNS domain parsing."""

import tempfile
import unittest
from pathlib import Path

from terok.lib.security.shield.dns import read_domains


class TestReadDomains(unittest.TestCase):
    """Tests for read_domains."""

    def test_reads_from_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("github.com\npypi.org\n")
            f.flush()
            domains = read_domains([Path(f.name)])
        self.assertEqual(domains, ["github.com", "pypi.org"])

    def test_skips_comments(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# this is a comment\ngithub.com\n# another\n")
            f.flush()
            domains = read_domains([Path(f.name)])
        self.assertEqual(domains, ["github.com"])

    def test_skips_empty_lines(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("github.com\n\n\npypi.org\n")
            f.flush()
            domains = read_domains([Path(f.name)])
        self.assertEqual(domains, ["github.com", "pypi.org"])

    def test_missing_file(self) -> None:
        domains = read_domains([Path("/nonexistent/file.txt")])
        self.assertEqual(domains, [])

    def test_multiple_files(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f1:
            f1.write("github.com\n")
            f1.flush()
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f2:
                f2.write("pypi.org\n")
                f2.flush()
                domains = read_domains([Path(f1.name), Path(f2.name)])
        self.assertEqual(domains, ["github.com", "pypi.org"])

    def test_empty_paths(self) -> None:
        domains = read_domains([])
        self.assertEqual(domains, [])
