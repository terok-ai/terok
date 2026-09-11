# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tier 2 integration tests: full end-to-end with real Podman.

These tests require ``podman`` and ``nft`` on PATH and are auto-skipped
when either is absent.  All operations are rootless — nftables runs in
the container's network namespace via ``podman unshare nsenter``.
"""

import pytest

terok_shield = pytest.importorskip("terok_shield")
Shield = terok_shield.Shield

from tests.testnet import ALLOWED_TARGET_DOMAIN, ALLOWED_TARGET_HTTP, TEST_IP_RFC5737

from .conftest import hooks_unavailable, nft_missing, podman_missing
from .helpers import assert_blocked, assert_reachable, inspect_container_json

pytestmark = pytest.mark.needs_podman


# Shield's tier-set names (the v15 tier engine replaced the flat
# ``allow_v4``/``deny_v4`` pair): a runtime ``allow`` lands in the t40
# project-allow tier, a runtime ``deny`` in the t20 security-deny tier.
_ALLOW_SET = "t40_project_allow_v4"
_DENY_SET = "t20_security_deny_v4"


def _nft_set(rules: str, name: str) -> str:
    """Return the body of the ``set <name> { ... }`` block from an ``nft list`` dump.

    Useful for assertions that need to know *which* tier set an IP lives
    in rather than just whether the IP appears anywhere in the rule text.
    Returns an empty string when the named set isn't found.
    """
    head = f"set {name} "
    if head not in rules:
        return ""
    _, _, after = rules.partition(head)
    body, _, _ = after.partition("set ")
    return body


# ── TestShieldEndToEnd ───────────────────────────────────


@podman_missing
@nft_missing
@hooks_unavailable
@pytest.mark.needs_hooks
class TestShieldEndToEnd:
    """End-to-end tests with a real container."""

    def test_container_starts_with_annotations(self, shielded_container: str) -> None:
        """Inspect container and verify shield annotations are present."""
        container_info = inspect_container_json(shielded_container)
        annotations = container_info.get("Config", {}).get("Annotations", {})
        assert "terok.shield.profiles" in annotations
        assert annotations["terok.shield.profiles"] == ""

    def test_shield_rules_returns_ruleset(
        self, shielded_container: str, real_shield: Shield
    ) -> None:
        """shield.rules returns a non-empty ruleset containing terok_shield."""
        output = real_shield.rules(shielded_container)
        assert "terok_shield" in output

    def test_allow_then_deny(self, shielded_container: str, real_shield: Shield) -> None:
        """``shield.allow`` puts an IP in the t40 tier; ``shield.deny`` moves it to t20.

        A deny is enforced as an explicit nftables set rather than
        silently dropping the IP from the allow tier — the IP is still
        present in the ruleset dump, just in a set the rules reject from,
        with a logged reason.
        """
        allowed = real_shield.allow(shielded_container, TEST_IP_RFC5737)
        assert TEST_IP_RFC5737 in allowed
        rules_after_allow = real_shield.rules(shielded_container)
        assert TEST_IP_RFC5737 in _nft_set(rules_after_allow, _ALLOW_SET)

        denied = real_shield.deny(shielded_container, TEST_IP_RFC5737)
        assert TEST_IP_RFC5737 in denied
        rules_after_deny = real_shield.rules(shielded_container)
        assert TEST_IP_RFC5737 not in _nft_set(rules_after_deny, _ALLOW_SET)
        assert TEST_IP_RFC5737 in _nft_set(rules_after_deny, _DENY_SET)


# ── TestShieldEgress ──────────────────────────────────────


@podman_missing
@nft_missing
@hooks_unavailable
@pytest.mark.needs_hooks
@pytest.mark.needs_internet
@pytest.mark.usefixtures("_verify_connectivity")
class TestShieldEgress:
    """Egress filtering tests (requires network connectivity)."""

    def test_egress_blocked_by_default(self, shielded_container: str) -> None:
        """Container cannot reach an external host by default."""
        assert_blocked(shielded_container, ALLOWED_TARGET_HTTP, timeout=5)

    def test_egress_allowed_after_allow(self, shielded_container: str, real_shield: Shield) -> None:
        """Container can reach a host after shield.allow."""
        real_shield.allow(shielded_container, ALLOWED_TARGET_DOMAIN)
        assert_reachable(shielded_container, ALLOWED_TARGET_HTTP, timeout=10)
