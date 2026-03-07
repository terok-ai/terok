# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for standard mode pre_start args."""

import unittest
import unittest.mock


class TestStandardPreStart(unittest.TestCase):
    """Tests for standard.pre_start return values."""

    @unittest.mock.patch("terok.lib.security.shield.standard._detect_rootless_network_mode")
    @unittest.mock.patch("terok.lib.security.shield.standard.resolve_and_write")
    @unittest.mock.patch("terok.lib.security.shield.standard.read_domains", return_value=[])
    @unittest.mock.patch("terok.lib.security.shield.standard.profile_path")
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pre_start_includes_cap_drops(
        self,
        _mock_euid: unittest.mock.Mock,
        _mock_profile: unittest.mock.Mock,
        _mock_read: unittest.mock.Mock,
        _mock_resolve: unittest.mock.Mock,
        mock_netmode: unittest.mock.Mock,
    ) -> None:
        """pre_start args include NET_ADMIN and NET_RAW cap drops."""
        mock_netmode.return_value = "pasta"
        from terok.lib.security.shield.standard import pre_start

        args = pre_start(["dev-standard"], "test-ctr", [], gate_port=9418)
        self.assertIn("--cap-drop", args)
        self.assertIn("NET_ADMIN", args)
        self.assertIn("NET_RAW", args)

    @unittest.mock.patch("terok.lib.security.shield.standard._detect_rootless_network_mode")
    @unittest.mock.patch("terok.lib.security.shield.standard.resolve_and_write")
    @unittest.mock.patch("terok.lib.security.shield.standard.read_domains", return_value=[])
    @unittest.mock.patch("terok.lib.security.shield.standard.profile_path")
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pre_start_includes_security_opt(
        self,
        _mock_euid: unittest.mock.Mock,
        _mock_profile: unittest.mock.Mock,
        _mock_read: unittest.mock.Mock,
        _mock_resolve: unittest.mock.Mock,
        mock_netmode: unittest.mock.Mock,
    ) -> None:
        """pre_start args include no-new-privileges."""
        mock_netmode.return_value = "pasta"
        from terok.lib.security.shield.standard import pre_start

        args = pre_start(["dev-standard"], "test-ctr", [], gate_port=9418)
        self.assertIn("no-new-privileges", args)

    @unittest.mock.patch("terok.lib.security.shield.standard._detect_rootless_network_mode")
    @unittest.mock.patch("terok.lib.security.shield.standard.resolve_and_write")
    @unittest.mock.patch("terok.lib.security.shield.standard.read_domains", return_value=[])
    @unittest.mock.patch("terok.lib.security.shield.standard.profile_path")
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pre_start_includes_annotation(
        self,
        _mock_euid: unittest.mock.Mock,
        _mock_profile: unittest.mock.Mock,
        _mock_read: unittest.mock.Mock,
        _mock_resolve: unittest.mock.Mock,
        mock_netmode: unittest.mock.Mock,
    ) -> None:
        """pre_start args include shield annotation."""
        mock_netmode.return_value = "pasta"
        from terok.lib.security.shield.standard import pre_start

        args = pre_start(["dev-standard"], "test-ctr", [], gate_port=9418)
        self.assertIn("--annotation", args)
        # Find the annotation value
        idx = args.index("--annotation")
        annotation = args[idx + 1]
        self.assertIn("terok.shield.profiles", annotation)
        self.assertIn("dev-standard", annotation)

    @unittest.mock.patch("terok.lib.security.shield.standard._detect_rootless_network_mode")
    @unittest.mock.patch("terok.lib.security.shield.standard.resolve_and_write")
    @unittest.mock.patch("terok.lib.security.shield.standard.read_domains", return_value=[])
    @unittest.mock.patch("terok.lib.security.shield.standard.profile_path")
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pre_start_pasta_network(
        self,
        _mock_euid: unittest.mock.Mock,
        _mock_profile: unittest.mock.Mock,
        _mock_read: unittest.mock.Mock,
        _mock_resolve: unittest.mock.Mock,
        mock_netmode: unittest.mock.Mock,
    ) -> None:
        """pre_start with pasta includes pasta network args with gate port forwarding."""
        mock_netmode.return_value = "pasta"
        from terok.lib.security.shield.standard import pre_start

        args = pre_start(["dev-standard"], "test-ctr", [], gate_port=9418)
        self.assertIn("--network", args)
        idx = args.index("--network")
        self.assertIn("pasta", args[idx + 1])
        self.assertIn("9418", args[idx + 1])

    @unittest.mock.patch("terok.lib.security.shield.standard._detect_rootless_network_mode")
    @unittest.mock.patch("terok.lib.security.shield.standard.resolve_and_write")
    @unittest.mock.patch("terok.lib.security.shield.standard.read_domains", return_value=[])
    @unittest.mock.patch("terok.lib.security.shield.standard.profile_path")
    @unittest.mock.patch("os.geteuid", return_value=1000)
    def test_pre_start_hooks_dir(
        self,
        _mock_euid: unittest.mock.Mock,
        _mock_profile: unittest.mock.Mock,
        _mock_read: unittest.mock.Mock,
        _mock_resolve: unittest.mock.Mock,
        mock_netmode: unittest.mock.Mock,
    ) -> None:
        """pre_start args include hooks-dir."""
        mock_netmode.return_value = "pasta"
        from terok.lib.security.shield.standard import pre_start

        args = pre_start(["dev-standard"], "test-ctr", [], gate_port=9418)
        self.assertIn("--hooks-dir", args)
