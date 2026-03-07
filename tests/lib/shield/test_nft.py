# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for nft.py — the auditable security boundary."""

import unittest

from terok.lib.security.shield.nft import (
    RFC1918,
    add_elements,
    create_set,
    forward_rule,
    hardened_ruleset,
    safe_ip,
    safe_name,
    standard_ruleset,
    verify_ruleset,
)


class TestSafeName(unittest.TestCase):
    """Tests for safe_name validator."""

    def test_valid_alphanumeric(self) -> None:
        self.assertEqual(safe_name("mycontainer"), "mycontainer")

    def test_hyphen_replaced(self) -> None:
        self.assertEqual(safe_name("my-container"), "my_container")

    def test_underscore_preserved(self) -> None:
        self.assertEqual(safe_name("my_container"), "my_container")

    def test_rejects_special_chars(self) -> None:
        with self.assertRaises(ValueError):
            safe_name("my;container")

    def test_rejects_spaces(self) -> None:
        with self.assertRaises(ValueError):
            safe_name("my container")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            safe_name("")

    def test_rejects_shell_injection(self) -> None:
        with self.assertRaises(ValueError):
            safe_name("$(whoami)")

    def test_rejects_braces(self) -> None:
        with self.assertRaises(ValueError):
            safe_name("test{evil}")


class TestSafeIp(unittest.TestCase):
    """Tests for safe_ip validator."""

    def test_valid_ipv4(self) -> None:
        self.assertEqual(safe_ip("192.168.1.1"), "192.168.1.1")

    def test_valid_cidr(self) -> None:
        self.assertEqual(safe_ip("10.0.0.0/8"), "10.0.0.0/8")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(safe_ip("  1.2.3.4  "), "1.2.3.4")

    def test_rejects_hostname(self) -> None:
        with self.assertRaises(ValueError):
            safe_ip("evil.com")

    def test_rejects_injection(self) -> None:
        with self.assertRaises(ValueError):
            safe_ip("1.2.3.4; drop")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            safe_ip("")

    def test_rejects_ipv6(self) -> None:
        with self.assertRaises(ValueError):
            safe_ip("::1")


class TestStandardRuleset(unittest.TestCase):
    """Tests for standard mode ruleset generation."""

    def test_contains_policy_drop(self) -> None:
        rs = standard_ruleset()
        self.assertIn("policy drop", rs)

    def test_contains_loopback_accept(self) -> None:
        rs = standard_ruleset()
        self.assertIn('oifname "lo" accept', rs)

    def test_contains_dns_accept(self) -> None:
        rs = standard_ruleset(dns="169.254.0.1")
        self.assertIn("169.254.0.1", rs)

    def test_contains_gate_port(self) -> None:
        rs = standard_ruleset(gate_port=9418)
        self.assertIn("tcp dport 9418", rs)

    def test_rfc1918_before_allow(self) -> None:
        rs = standard_ruleset()
        rfc_pos = rs.index("10.0.0.0/8")
        allow_pos = rs.index("@allow_v4")
        self.assertLess(rfc_pos, allow_pos, "RFC1918 must appear before allow set")

    def test_all_rfc1918_present(self) -> None:
        rs = standard_ruleset()
        for net in RFC1918:
            self.assertIn(net, rs)

    def test_deny_log_present(self) -> None:
        rs = standard_ruleset()
        self.assertIn("TEROK_SHIELD_DENIED", rs)

    def test_reject_type_present(self) -> None:
        rs = standard_ruleset()
        self.assertIn("admin-prohibited", rs)

    def test_audit_allow_present(self) -> None:
        rs = standard_ruleset()
        self.assertIn("TEROK_SHIELD_ALLOWED", rs)

    def test_input_chain_present(self) -> None:
        rs = standard_ruleset()
        self.assertIn("chain input", rs)

    def test_rejects_invalid_dns(self) -> None:
        with self.assertRaises(ValueError):
            standard_ruleset(dns="not-an-ip")

    def test_custom_gate_port(self) -> None:
        rs = standard_ruleset(gate_port=12345)
        self.assertIn("tcp dport 12345", rs)


class TestHardenedRuleset(unittest.TestCase):
    """Tests for hardened mode ruleset generation."""

    def test_contains_policy_drop(self) -> None:
        rs = hardened_ruleset()
        self.assertIn("policy drop", rs)

    def test_forward_chain(self) -> None:
        rs = hardened_ruleset()
        self.assertIn("chain forward", rs)

    def test_contains_bridge_gateway(self) -> None:
        rs = hardened_ruleset(gw="10.91.0.1")
        self.assertIn("10.91.0.1", rs)

    def test_contains_bridge_subnet(self) -> None:
        rs = hardened_ruleset(subnet="10.91.0.0/24")
        self.assertIn("10.91.0.0/24", rs)

    def test_all_rfc1918_present(self) -> None:
        rs = hardened_ruleset()
        for net in RFC1918:
            self.assertIn(net, rs)

    def test_rfc1918_before_subnet_accept(self) -> None:
        rs = hardened_ruleset()
        rfc_pos = rs.index("10.0.0.0/8")
        # Find the intra-bridge accept (not an RFC1918 rule)
        subnet_pos = rs.index("@global_allow_v4")
        self.assertLess(rfc_pos, subnet_pos)

    def test_gate_port_present(self) -> None:
        rs = hardened_ruleset(gate_port=9418)
        self.assertIn("tcp dport 9418", rs)

    def test_rejects_invalid_gw(self) -> None:
        with self.assertRaises(ValueError):
            hardened_ruleset(gw="not-an-ip")


class TestAddElements(unittest.TestCase):
    """Tests for add_elements."""

    def test_valid_ips(self) -> None:
        result = add_elements("allow_v4", ["1.2.3.4", "5.6.7.8"])
        self.assertIn("1.2.3.4", result)
        self.assertIn("5.6.7.8", result)

    def test_empty_list(self) -> None:
        result = add_elements("allow_v4", [])
        self.assertEqual(result, "")

    def test_skips_invalid(self) -> None:
        result = add_elements("allow_v4", ["1.2.3.4", "invalid", "5.6.7.8"])
        self.assertIn("1.2.3.4", result)
        self.assertIn("5.6.7.8", result)
        self.assertNotIn("invalid", result)

    def test_all_invalid(self) -> None:
        result = add_elements("allow_v4", ["bad", "worse"])
        self.assertEqual(result, "")


class TestCreateSet(unittest.TestCase):
    """Tests for create_set."""

    def test_basic(self) -> None:
        result = create_set("mycontainer")
        self.assertIn("mycontainer_allow_v4", result)

    def test_hyphen_normalized(self) -> None:
        result = create_set("my-container")
        self.assertIn("my_container_allow_v4", result)


class TestForwardRule(unittest.TestCase):
    """Tests for forward_rule."""

    def test_basic(self) -> None:
        result = forward_rule("mycontainer", "10.91.0.5")
        self.assertIn("10.91.0.5", result)
        self.assertIn("mycontainer_allow_v4", result)
        self.assertIn("terok_shield:mycontainer", result)


class TestVerifyRuleset(unittest.TestCase):
    """Tests for verify_ruleset."""

    def test_valid_ruleset(self) -> None:
        rs = standard_ruleset()
        errors = verify_ruleset(rs)
        self.assertEqual(errors, [])

    def test_missing_policy(self) -> None:
        errors = verify_ruleset("some random text")
        self.assertTrue(any("policy" in e for e in errors))

    def test_missing_rfc1918(self) -> None:
        errors = verify_ruleset("policy drop admin-prohibited TEROK_SHIELD_DENIED")
        rfc_errors = [e for e in errors if "RFC1918" in e]
        self.assertEqual(len(rfc_errors), len(RFC1918))

    def test_empty_input(self) -> None:
        errors = verify_ruleset("")
        self.assertGreater(len(errors), 0)
