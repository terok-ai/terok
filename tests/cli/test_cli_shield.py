# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for shield CLI command dispatch."""

import argparse
import unittest
import unittest.mock
from io import StringIO

from terok.cli.commands.shield import dispatch, register


class TestShieldRegister(unittest.TestCase):
    """Tests for shield CLI registration."""

    def test_register_adds_shield(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["shield", "status"])
        self.assertEqual(args.cmd, "shield")
        self.assertEqual(args.shield_cmd, "status")

    def test_register_setup_hardened(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        register(sub)
        args = parser.parse_args(["shield", "setup", "--hardened"])
        self.assertTrue(args.hardened)


class TestShieldDispatch(unittest.TestCase):
    """Tests for shield CLI dispatch."""

    def test_non_shield_returns_false(self) -> None:
        args = argparse.Namespace(cmd="other")
        self.assertFalse(dispatch(args))

    @unittest.mock.patch("terok.cli.commands.shield.shield_setup")
    def test_setup_dispatches(self, mock_setup: unittest.mock.Mock) -> None:
        args = argparse.Namespace(cmd="shield", shield_cmd="setup", hardened=False)
        with unittest.mock.patch("sys.stdout", new_callable=StringIO):
            result = dispatch(args)
        self.assertTrue(result)
        mock_setup.assert_called_once_with(hardened=False)

    @unittest.mock.patch("terok.cli.commands.shield.shield_status")
    def test_status_dispatches(self, mock_status: unittest.mock.Mock) -> None:
        mock_status.return_value = {
            "mode": "standard",
            "profiles": ["dev-standard"],
            "audit_enabled": True,
            "log_files": [],
        }
        args = argparse.Namespace(cmd="shield", shield_cmd="status")
        with unittest.mock.patch("sys.stdout", new_callable=StringIO):
            result = dispatch(args)
        self.assertTrue(result)
        mock_status.assert_called_once()
