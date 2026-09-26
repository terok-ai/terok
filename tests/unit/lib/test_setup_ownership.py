# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terok certifies its own setup and composes only public downward checks."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from terok_util import SetupCheck, SetupDowngradeError, SetupRequiredError, SetupStatus

from terok.lib.core import setup


@pytest.fixture
def child_checks():
    """Isolate dependency readiness from the installed executor release."""
    check = Mock(return_value=())
    with (
        patch.object(setup, "executor", SimpleNamespace(check_setup=check)),
        patch.object(setup, "version", return_value="1.0.0"),
    ):
        yield check


def test_receipt_is_owner_local_and_composes_child_results(child_checks) -> None:
    """No child versions or receipts leak into the terok-owned receipt."""
    setup.complete_setup()
    receipt = setup._receipt()
    assert json.loads(receipt.path.read_text()) == {
        "owner": "terok",
        "version": "1.0.0",
        "inputs": {"desktop_policy": "auto"},
    }
    child = SetupCheck("executor", "routes", SetupStatus.STALE, "routes changed")
    child_checks.return_value = (child,)
    assert setup.check_setup() == (receipt.check(), child)
    child_checks.assert_called_with(setup.make_sandbox_config(), live=False)


def test_owner_upgrade_or_config_change_requires_setup(child_checks) -> None:
    """Only terok's version and relevant local inputs affect its receipt."""
    setup.complete_setup()
    with patch.object(setup, "version", return_value="1.0.1"):
        assert setup.check_setup()[0].status is SetupStatus.STALE
    with patch.object(setup, "get_tui_desktop_entry", return_value="skip"):
        assert setup.check_setup()[0].status is SetupStatus.STALE


@pytest.mark.parametrize("owner", ["terok", "executor"])
def test_preflight_rejects_full_closure_downgrade_without_writing(child_checks, owner) -> None:
    """An upper or lower newer receipt blocks setup before invalidation."""
    setup.complete_setup()
    before = setup._receipt().path.read_bytes()
    if owner == "executor":
        child_checks.return_value = (SetupCheck(owner, "receipt", SetupStatus.DOWNGRADE),)
    with patch.object(setup, "version", return_value="0.9.0" if owner == "terok" else "1.0.0"):
        with pytest.raises(SetupDowngradeError):
            setup.preflight_setup()
    assert setup._receipt().path.read_bytes() == before


def test_failed_upper_write_does_not_recertify_or_clear_child_receipts(child_checks) -> None:
    """Invalidation precedes work; failure leaves terok missing without mutating children."""
    setup.complete_setup()
    setup.invalidate_setup()
    with patch("terok_util.setup.os.replace", side_effect=OSError("read-only")):
        with pytest.raises(OSError, match="read-only"):
            setup.complete_setup()
    assert setup.check_setup()[0].status is SetupStatus.MISSING


def test_incomplete_dependency_prevents_certification(child_checks) -> None:
    """Partial lower setup cannot produce a successful upper receipt."""
    child_checks.return_value = (SetupCheck("executor", "routes", SetupStatus.MISSING),)
    with pytest.raises(SetupRequiredError):
        setup.complete_setup()
    assert not setup._receipt().path.exists()


def test_live_validation_checks_dependencies_even_with_ready_owner_receipt(child_checks) -> None:
    """A disappeared host executable is rejected before managed launch."""
    setup.complete_setup()
    child_checks.reset_mock()
    child_checks.return_value = (SetupCheck("shield", "nft", SetupStatus.MISSING),)
    with pytest.raises(SetupRequiredError):
        setup.validate_host_setup()
    child_checks.assert_called_once_with(setup.make_sandbox_config(), live=True)


def test_checks_preserve_the_launch_configuration(child_checks) -> None:
    """The downward check receives terok's explicit Shield opt-out, not library defaults."""
    cfg = setup.make_sandbox_config()
    from dataclasses import replace

    cfg = replace(cfg, shield_disabled=True)
    with patch.object(setup, "make_sandbox_config", return_value=cfg):
        setup.complete_setup()
        child_checks.assert_called_with(cfg)
        setup.check_setup(live=True)
        child_checks.assert_called_with(cfg, live=True)


def test_legacy_aggregate_stamp_cannot_certify_terok(child_checks) -> None:
    """Upgrade requires new owner-local certification instead of a legacy stamp reader."""
    receipt = setup._receipt()
    receipt.path.parent.mkdir(parents=True)
    receipt.path.write_text(json.dumps({"terok": "1.0.0", "terok-executor": "1.0.0"}))
    assert setup.check_setup()[0].status is SetupStatus.INVALID
