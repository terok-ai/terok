# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The degraded-DNS-tier warning: flag a task launched on dig/getent."""

from __future__ import annotations

import pytest

from terok.lib.core.task_display import DnsTierWarning, dns_tier_warning


@pytest.mark.parametrize("task_tier", ["dnsmasq", None, "", "nonsense"])
def test_no_warning_when_task_tier_is_not_degraded(task_tier: str | None) -> None:
    """A healthy (or unknown) task tier never raises a warning."""
    assert dns_tier_warning(task_tier) is None


@pytest.mark.parametrize("task_tier", ["dig", "getent"])
def test_degraded_tier_is_flagged_with_its_name(task_tier: str) -> None:
    """A static-allowlist tier is flagged, its headline naming the tier."""
    warn = dns_tier_warning(task_tier)
    assert isinstance(warn, DnsTierWarning)
    assert warn.tier == task_tier
    assert warn.headline == f"{task_tier} (degraded)"
    assert warn.detail  # non-empty operator explanation


def test_warning_is_frozen() -> None:
    """The warning value object is immutable."""
    warn = DnsTierWarning(tier="dig", detail="x")
    with pytest.raises((AttributeError, TypeError)):
        warn.tier = "getent"  # type: ignore[misc]
