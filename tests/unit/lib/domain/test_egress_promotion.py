# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The promotion engine: reviewing a task's harvest and moving one target into policy.

The tier the shield classified a target under decides where it may go, so the
heart of these tests is the gating matrix — every standing a harvest entry can
have, against every destination — with the refused half asserting that nothing
was written and nothing was asked of the shield.  Around it: merging the
harvest per target, the set suggestions and their ranking, what each
destination writes and applies live, and the export user groups send back.

The project is real (``project.yml`` on disk, written by the real writers); the
task's shield is a double, since a harvest and a live grant need a container.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from tests.test_utils import project_env, write_project
from tests.testfs import MOCK_BASE
from tests.testnet import (
    ALLOWED_TARGET_IPS,
    ALT_HTTPS_PORT,
    CLOUD_METADATA_IP,
    HTTPS_PORT,
    PRIVATE_LAN_IP,
    TEST_IP,
    TEST_UPSTREAM_URL,
)

from terok.lib.api.shield import (
    REMEDY_HOST_PORT,
    REMEDY_OVERRIDE,
    REMEDY_PROJECT,
    REMEDY_SET,
    REMEDY_TASK,
    TIER_ALLOWLIST,
    TIER_HARD_DENY,
    TIER_OPERATOR_DENY,
    TIER_PRIVATE,
    TIER_SECURITY,
    AllowResult,
    DnsTier,
    HarvestEntry,
    HarvestReport,
    PromotionRefused,
    SecurityTierRefusal,
    export_egress,
    promote_egress,
    review_egress,
    tier_heading,
)
from terok.lib.core.config import egress_sets_dir
from terok.lib.core.projects import load_project
from terok.lib.orchestration.tasks import container_name

_ENGINE = "terok.lib.domain.egress_promotion"
_NAME = "proj-promote"
_TASK = "t1"
_MODE = "cli"
_CONTAINER = container_name(_NAME, _MODE, _TASK)
_YML = f'project:\n  name: {_NAME}\ngit:\n  upstream_url: "{TEST_UPSTREAM_URL}"\n'
_FIRST = "2026-10-01T10:00:00+00:00"
_LAST = "2026-10-02T11:00:00+00:00"
_HOST = "svc.example.org"
"""A host no egress set names and the project does not allow."""
_USER_SET = "team-mirrors"
_REASON = "vendor outage, direct debugging"
_UNTIL = date.today() + timedelta(days=7)

_STANDINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "allowlist": (TIER_ALLOWLIST, (REMEDY_TASK, REMEDY_PROJECT, REMEDY_SET)),
    "operator-deny": (TIER_OPERATOR_DENY, (REMEDY_TASK, REMEDY_PROJECT)),
    "security": (TIER_SECURITY, (REMEDY_OVERRIDE,)),
    "private": (TIER_PRIVATE, (REMEDY_OVERRIDE,)),
    "hard-deny": (TIER_HARD_DENY, ()),
    "host-loopback": (TIER_HARD_DENY, (REMEDY_HOST_PORT,)),
}
"""Every standing a harvest entry can have, with the remedies shield attaches to it."""

_DESTINATIONS = (REMEDY_TASK, REMEDY_PROJECT, REMEDY_SET, REMEDY_OVERRIDE)


def _entry(
    target: str,
    *,
    standing: str = "allowlist",
    action: str = "blocked",
    count: int = 1,
    ports: tuple[int, ...] = (HTTPS_PORT,),
    addresses: tuple[str, ...] = (),
    first: str = _FIRST,
    last: str = _LAST,
) -> HarvestEntry:
    """One harvest line, classified the way shield classifies *standing*."""
    tier, remedies = _STANDINGS[standing]
    return HarvestEntry(
        action=action,
        target=target,
        count=count,
        first_seen=first,
        last_seen=last,
        ports=ports,
        addresses=addresses,
        tier=tier,
        remedies=remedies,
    )


def _manager(
    entries: Sequence[HarvestEntry],
    *,
    tier: DnsTier | None = DnsTier.DNSMASQ_LIVE,
    running: bool = True,
    audit: bool = True,
) -> MagicMock:
    """A task shield manager double: its harvest, its tier, and grants that land like shield's."""
    manager = MagicMock()
    manager.harvest.return_value = HarvestReport(entries=tuple(entries), audit_enabled=audit)
    manager.dns_tier = tier
    manager.allow.side_effect = lambda _container, target: AllowResult(
        target=target, ips=(TEST_IP,) if running else (), live=running
    )
    manager.override.return_value = [TEST_IP]
    return manager


@contextmanager
def _task(
    entries: Sequence[HarvestEntry] = (),
    *,
    yml: str = _YML,
    tier: DnsTier | None = DnsTier.DNSMASQ_LIVE,
    running: bool = True,
    audit: bool = True,
) -> Iterator[SimpleNamespace]:
    """Lay out project ``_NAME`` with task ``_TASK`` whose shield harvested *entries*."""
    manager = _manager(entries, tier=tier, running=running, audit=audit)
    with (
        project_env(yml, project_name=_NAME) as env,
        patch(f"{_ENGINE}.task_shield_manager", return_value=manager),
        patch(f"{_ENGINE}.load_task_meta", return_value=({"mode": _MODE}, MOCK_BASE / "meta")),
        patch(
            f"{_ENGINE}.get_task_container_state",
            return_value="running" if running else "exited",
        ),
    ):
        yield SimpleNamespace(
            env=env, manager=manager, path=env.config_root / _NAME / "project.yml"
        )


def _user_set_path(name: str = _USER_SET) -> Path:
    """Where user set *name* lives in the test-isolated config directory."""
    return egress_sets_dir() / f"{name}.txt"


def _write_user_set(name: str, text: str) -> Path:
    """Write a user egress set into the test-isolated egress-sets directory."""
    path = _user_set_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _destination_kwargs(destination: str) -> dict[str, object]:
    """The keyword arguments a promotion to *destination* takes."""
    if destination == REMEDY_SET:
        return {"set_name": _USER_SET}
    if destination == REMEDY_OVERRIDE:
        return {"reason": _REASON, "expires": _UNTIL}
    return {}


# ── The gating matrix: every standing × every destination ──────────────


@pytest.mark.parametrize("destination", _DESTINATIONS)
@pytest.mark.parametrize("standing", list(_STANDINGS))
def test_the_tier_decides_every_destination(standing: str, destination: str) -> None:
    """A destination the standing's remedies name goes through; any other is refused untouched."""
    _tier, remedies = _STANDINGS[standing]
    with _task([_entry(_HOST, standing=standing)]) as task:
        before = task.path.read_text(encoding="utf-8")
        kwargs = _destination_kwargs(destination)
        if destination not in remedies:
            with pytest.raises(PromotionRefused):
                promote_egress(_NAME, _TASK, _HOST, destination, **kwargs)  # type: ignore[arg-type]
            assert task.path.read_text(encoding="utf-8") == before
            task.manager.allow.assert_not_called()
            task.manager.override.assert_not_called()
            assert not _user_set_path().exists()
            return
        result = promote_egress(_NAME, _TASK, _HOST, destination, **kwargs)  # type: ignore[arg-type]
        assert result.severity == "information", result.message
        assert result.destination == destination
        project = load_project(_NAME)
    if destination == REMEDY_TASK:
        task.manager.allow.assert_called_once_with(_CONTAINER, _HOST)
    elif destination == REMEDY_PROJECT:
        assert project.shield_allow == (_HOST,)
    elif destination == REMEDY_SET:
        assert project.shield_sets == (_USER_SET,)
        assert _HOST in _user_set_path().read_text(encoding="utf-8")
    else:
        (override,) = project.shield_override
        assert (override.host, override.reason, override.expires) == (_HOST, _REASON, _UNTIL)


@pytest.mark.parametrize(
    ("standing", "words"),
    [
        pytest.param("security", "security tier", id="security"),
        pytest.param("private", "private network range", id="private"),
        pytest.param("hard-deny", "hard-deny floor", id="hard-deny"),
        pytest.param("host-loopback", "+localhost:PORT", id="host-loopback"),
        pytest.param("operator-deny", "your own runtime deny", id="operator-deny"),
    ],
)
def test_a_refusal_names_the_route_that_is_open(standing: str, words: str) -> None:
    """The refusal says why, and what — if anything — reaches the target instead."""
    with _task([_entry(_HOST, standing=standing)]) as task:
        with pytest.raises(PromotionRefused, match=words.replace("+", r"\+")):
            promote_egress(_NAME, _TASK, _HOST, REMEDY_SET, set_name=_USER_SET)
        task.manager.allow.assert_not_called()


def test_an_override_is_refused_for_what_nothing_but_the_allowlist_refuses() -> None:
    """Least privilege: a t40 miss never escalates above the security tier."""
    with _task([_entry(_HOST)]):
        with pytest.raises(PromotionRefused, match="for no reason"):
            promote_egress(_NAME, _TASK, _HOST, REMEDY_OVERRIDE, reason=_REASON, expires=_UNTIL)


def test_a_target_must_come_from_the_harvest() -> None:
    """Promotion reviews evidence; it is no general allowlist editor."""
    with _task([_entry(_HOST)]) as task:
        before = task.path.read_text(encoding="utf-8")
        with pytest.raises(PromotionRefused, match="not among what task"):
            promote_egress(_NAME, _TASK, "elsewhere.example.org", REMEDY_PROJECT)
        assert task.path.read_text(encoding="utf-8") == before


def test_a_target_matches_its_harvest_entry_case_insensitively() -> None:
    """DNS names compare without case, so a typed ``SVC.Example.org`` is the harvested name."""
    with _task([_entry(_HOST)]) as task:
        promote_egress(_NAME, _TASK, _HOST.upper(), REMEDY_TASK)
        task.manager.allow.assert_called_once_with(_CONTAINER, _HOST)


@pytest.mark.parametrize(
    ("target", "destination", "kwargs", "words"),
    [
        pytest.param(_HOST, "host_port", {}, "no destination", id="unknown-destination"),
        pytest.param(_HOST, REMEDY_TASK, {"reason": _REASON}, "override only", id="reason-task"),
        pytest.param(_HOST, REMEDY_PROJECT, {"expires": _UNTIL}, "override only", id="expires"),
        pytest.param(_HOST, REMEDY_PROJECT, {"set_name": "x"}, "set name", id="set-name"),
        pytest.param(_HOST, REMEDY_SET, {}, "set name", id="set-without-name"),
        pytest.param(None, REMEDY_TASK, {}, "needs a target", id="no-target"),
        pytest.param(_HOST, REMEDY_OVERRIDE, {"reason": _REASON}, "reason", id="no-expiry"),
        pytest.param(
            _HOST, REMEDY_OVERRIDE, {"reason": "  ", "expires": _UNTIL}, "reason", id="blank"
        ),
    ],
)
def test_arguments_that_do_not_fit_the_destination_are_refused_before_any_read(
    target: str | None, destination: str, kwargs: dict[str, object], words: str
) -> None:
    """The request is checked first: no harvest is read, no file touched."""
    with patch(f"{_ENGINE}.load_project", side_effect=AssertionError("read nothing")):
        with pytest.raises(PromotionRefused, match=words):
            promote_egress(_NAME, _TASK, target, destination, **kwargs)  # type: ignore[arg-type]


# ── Review: merging, ordering, suggestions ─────────────────────────────


def test_review_merges_a_targets_lines_across_actions() -> None:
    """Shield keys lines by (action, target); a promotion decides per target."""
    entries = [
        _entry(_HOST, count=2, ports=(HTTPS_PORT,), addresses=(TEST_IP,), last=_FIRST),
        _entry(
            _HOST,
            action="bypass",
            count=3,
            ports=(ALT_HTTPS_PORT,),
            addresses=(ALLOWED_TARGET_IPS[0],),
            first=_LAST,
            last=_LAST,
        ),
    ]
    with _task(entries):
        review = review_egress(_NAME, _TASK)
    (candidate,) = review.candidates
    assert candidate.actions == ("blocked", "bypass")
    assert candidate.count == 5
    assert candidate.ports == (HTTPS_PORT, ALT_HTTPS_PORT)
    assert candidate.addresses == (TEST_IP, ALLOWED_TARGET_IPS[0])
    assert (candidate.first_seen, candidate.last_seen) == (_FIRST, _LAST)


def test_the_strictest_standing_wins_a_merge() -> None:
    """A name seen once at a security-tier address is a security-tier target."""
    entries = [_entry(_HOST), _entry(_HOST, action="down", standing="security")]
    with _task(entries):
        (candidate,) = review_egress(_NAME, _TASK).candidates
    assert candidate.tier == TIER_SECURITY
    assert candidate.destinations == (REMEDY_OVERRIDE,)


def test_a_merge_within_one_tier_keeps_only_what_every_line_allows() -> None:
    """The hard-deny floor with and without the host-loopback remedy offers nothing."""
    entries = [
        _entry(CLOUD_METADATA_IP, standing="hard-deny"),
        _entry(CLOUD_METADATA_IP, action="down", standing="host-loopback"),
    ]
    with _task(entries):
        (candidate,) = review_egress(_NAME, _TASK).candidates
    assert candidate.remedies == ()


def test_a_tier_this_terok_does_not_know_is_ranked_strictest_and_listed_last() -> None:
    """A newer shield's tier is offered only what shield itself attached, under a plain heading."""
    future = HarvestEntry(
        action="blocked",
        target=_HOST,
        count=1,
        first_seen=_FIRST,
        last_seen=_LAST,
        ports=(HTTPS_PORT,),
        addresses=(),
        tier="t15_future",
        remedies=(REMEDY_TASK,),
    )
    entries = [future, _entry(_HOST), _entry("pypi.org", standing="security")]
    with _task(entries):
        review = review_egress(_NAME, _TASK)
    assert [c.target for c in review.candidates] == ["pypi.org", _HOST]
    assert review.candidates[-1].tier == "t15_future"
    assert review.candidates[-1].destinations == (REMEDY_TASK,)
    assert "t15_future" in tier_heading("t15_future")


def test_review_lists_promotable_first_and_the_floor_last() -> None:
    """Review order: allowlist, operator deny, security, private, floor — loudest first within."""
    entries = [
        _entry(CLOUD_METADATA_IP, standing="hard-deny", count=9),
        _entry("api.vendor.example", standing="security", count=4),
        _entry("quiet.example.org", count=1),
        _entry("loud.example.org", count=7),
        _entry(PRIVATE_LAN_IP, standing="private", count=2),
        _entry("held.example.org", standing="operator-deny"),
    ]
    with _task(entries):
        review = review_egress(_NAME, _TASK)
    assert [c.target for c in review.candidates] == [
        "loud.example.org",
        "quiet.example.org",
        "held.example.org",
        "api.vendor.example",
        PRIVATE_LAN_IP,
        CLOUD_METADATA_IP,
    ]


def test_suggestions_cover_the_most_first_then_the_smaller_set() -> None:
    """Greedy: python covers two; node and rust one each, and node grants fewer hosts."""
    entries = [_entry(t) for t in ("pypi.org", "files.pythonhosted.org", "crates.io")]
    entries.append(_entry("registry.npmjs.org"))
    with _task(entries):
        review = review_egress(_NAME, _TASK)
    assert [s.name for s in review.suggestions] == ["python", "node", "rust"]
    python = review.suggestions[0]
    assert python.covers == ("files.pythonhosted.org", "pypi.org")
    assert (python.of, python.hosts) == (4, 2)
    assert "covers 2 of 4" in python.summary
    assert review.candidate("pypi.org").suggested_set == "python"  # type: ignore[union-attr]


def test_a_built_in_set_wins_a_tie_with_your_own() -> None:
    """Same coverage, same size: the built-in set means the same on every host."""
    _write_user_set("py-mirror", "pypi.org\nfiles.pythonhosted.org\n")
    entries = [_entry("pypi.org"), _entry("files.pythonhosted.org")]
    with _task(entries):
        review = review_egress(_NAME, _TASK)
    assert [s.name for s in review.suggestions] == ["python"]


def test_your_own_set_wins_when_it_covers_more() -> None:
    """Coverage ranks first, whatever the origin."""
    _write_user_set(_USER_SET, f"pypi.org\nfiles.pythonhosted.org\n{_HOST}\n")
    entries = [_entry("pypi.org"), _entry("files.pythonhosted.org"), _entry(_HOST)]
    with _task(entries):
        review = review_egress(_NAME, _TASK)
    assert [(s.name, s.origin) for s in review.suggestions] == [(_USER_SET, "user")]


def test_an_enabled_set_is_not_suggested_and_marks_what_it_grants() -> None:
    """A refusal of a granted target is a restart or a stale pin, not a missing set."""
    yml = _YML + "shield:\n  sets: [python]\n  allow: [granted.example.org]\n"
    entries = [_entry("pypi.org"), _entry("granted.example.org"), _entry("crates.io")]
    with _task(entries, yml=yml):
        review = review_egress(_NAME, _TASK)
    assert [s.name for s in review.suggestions] == ["rust"]
    assert review.candidate("pypi.org").covered_by == "the egress set 'python'"  # type: ignore[union-attr]
    assert review.candidate("granted.example.org").covered_by == "shield.allow"  # type: ignore[union-attr]
    assert review.candidate("crates.io").covered_by is None  # type: ignore[union-attr]


def test_only_an_override_covers_a_security_tier_target() -> None:
    """An allow sits below the security tier, so it grants a vault host nothing."""
    yml = _YML + (
        "shield:\n  allow: [api.vendor.example]\n  override:\n"
        f"    - host: api.vendor.example\n      reason: x\n      expires: {_UNTIL.isoformat()}\n"
    )
    with _task([_entry("api.vendor.example", standing="security")], yml=yml):
        (candidate,) = review_egress(_NAME, _TASK).candidates
    assert candidate.covered_by == "shield.override"


def test_subdomains_are_covered_only_on_the_live_tier() -> None:
    """dnsmasq-live admits a listed domain's subdomains; a tier that pins names does not."""
    entries = [_entry("api.github.com")]
    with _task(entries, tier=DnsTier.DNSMASQ_LIVE):
        live = review_egress(_NAME, _TASK)
    with _task(entries, tier=DnsTier.DNSMASQ_STATIC):
        static = review_egress(_NAME, _TASK)
    assert [s.name for s in live.suggestions] == ["git-hosting"]
    assert static.suggestions == ()
    assert static.domain_attribution


@pytest.mark.parametrize("tier", [DnsTier.LOOKUP, DnsTier.GETENT])
def test_a_tier_without_names_suggests_nothing_and_says_why(tier: DnsTier) -> None:
    """No dnsmasq, no names: no set can be matched to an address."""
    with _task([_entry("pypi.org"), _entry(TEST_IP)], tier=tier) as task:
        review = review_egress(_NAME, _TASK)
        result = promote_egress(_NAME, _TASK, TEST_IP, REMEDY_TASK)
        task.manager.allow.assert_called_once_with(_CONTAINER, TEST_IP)
    assert review.suggestions == ()
    assert not review.domain_attribution
    assert tier.value in str(review.attribution_note)
    assert result.severity == "warning"
    assert "records no names" in result.message


def test_an_empty_review_says_whether_it_could_have_held_anything() -> None:
    """With audit off nothing was recorded, so emptiness is no all-clear."""
    with _task(audit=False):
        silent = review_egress(_NAME, _TASK)
    with _task():
        clean = review_egress(_NAME, _TASK)
    assert "audit was off" in str(silent.empty_reason)
    assert "refused nothing" in str(clean.empty_reason)
    assert clean.attribution_note is None


def test_a_review_round_trips_through_json() -> None:
    """``--json`` and the TUI read the same data: every closed destination says why."""
    with _task([_entry("pypi.org"), _entry("api.vendor.example", standing="security")]):
        data = json.loads(json.dumps(review_egress(_NAME, _TASK).as_dict()))
    assert data["suggestions"][0]["set"] == "python"
    vault = next(c for c in data["candidates"] if c["target"] == "api.vendor.example")
    assert vault["destinations"] == [REMEDY_OVERRIDE]
    assert set(vault["refusals"]) == {REMEDY_TASK, REMEDY_PROJECT, REMEDY_SET}


# ── Destination: this task ─────────────────────────────────────────────


def test_a_running_task_takes_the_grant_now() -> None:
    with _task([_entry(_HOST)]) as task:
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)
        before = task.path.read_text(encoding="utf-8")
    assert result.live and not result.next_start
    assert TEST_IP in result.message
    assert before == _YML  # a task grant writes no project file


def test_a_stopped_task_gets_the_grant_at_its_next_start() -> None:
    """Decision I: shield records it in the task's overlay for the restart to apply."""
    with _task([_entry(_HOST)], running=False) as task:
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)
        task.manager.allow.assert_called_once_with(_CONTAINER, _HOST)
    assert result.next_start and not result.live
    assert "next start" in result.message


def test_a_live_grant_that_took_no_address_is_a_failure() -> None:
    with _task([_entry(_HOST)]) as task:
        task.manager.allow.side_effect = None
        task.manager.allow.return_value = AllowResult(target=_HOST, ips=(), live=True)
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)
    assert result.failed and result.severity == "error"


def test_a_security_tier_refusal_from_the_shield_points_at_break_glass() -> None:
    """Shield re-checks the tier with a fresh resolution — a shared address refuses too."""
    with _task([_entry(_HOST)]) as task:
        task.manager.allow.side_effect = SecurityTierRefusal(
            _HOST, entry="api.vendor.example", address=TEST_IP
        )
        with pytest.raises(PromotionRefused, match="break-glass override"):
            promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)


def test_the_kill_switch_refusal_propagates() -> None:
    """A disabled shield opens nothing, and says so — it is no promotion refusal."""
    with _task([_entry(_HOST)]) as task:
        task.manager.allow.side_effect = RuntimeError("shield is disabled")
        with pytest.raises(RuntimeError, match="disabled"):
            promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)


def test_an_operator_deny_lifted_for_the_task_says_so() -> None:
    with _task([_entry(_HOST, standing="operator-deny")]):
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)
    assert "deny of it is lifted" in result.message


# ── Destination: the project ───────────────────────────────────────────


def test_a_project_promotion_reaches_the_running_task_now() -> None:
    with _task([_entry(_HOST)]) as task:
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        task.manager.allow.assert_called_once_with(_CONTAINER, _HOST)
        assert load_project(_NAME).shield_allow == (_HOST,)
    assert result.live and result.next_start
    assert result.paths == (task.path,)


def test_a_stopped_task_picks_a_project_promotion_up_at_its_next_start() -> None:
    """Nothing to apply live, and no sticky overlay entry the project file already carries."""
    with _task([_entry(_HOST)], running=False) as task:
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        task.manager.allow.assert_not_called()
    assert not result.live
    assert "not running" in result.message


def test_a_project_promotion_lifts_the_tasks_own_deny_even_when_stopped() -> None:
    """The task's runtime deny sits above shield.allow — the grant would not reach it otherwise."""
    with _task([_entry(_HOST, standing="operator-deny")], running=False) as task:
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        task.manager.allow.assert_called_once_with(_CONTAINER, _HOST)
    assert "Lifted task" in result.message


def test_a_failed_live_apply_leaves_the_project_grant_and_warns() -> None:
    with _task([_entry(_HOST)]) as task:
        task.manager.allow.side_effect = RuntimeError("shield is disabled")
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        assert load_project(_NAME).shield_allow == (_HOST,)
    assert result.severity == "warning"
    assert "shield is disabled" in result.message


def test_a_project_promotion_of_a_granted_host_writes_nothing_but_still_applies() -> None:
    yml = _YML + f"shield:\n  allow: [{_HOST}]\n"
    with _task([_entry(_HOST)], yml=yml) as task:
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        assert task.path.read_text(encoding="utf-8") == yml
        task.manager.allow.assert_called_once()
    assert result.paths == ()
    assert "nothing written" in result.message


# ── Destination: an egress set ─────────────────────────────────────────


def test_a_built_in_set_naming_the_target_is_enabled() -> None:
    with _task([_entry("pypi.org")]):
        result = promote_egress(_NAME, _TASK, "pypi.org", REMEDY_SET, set_name="python")
        assert load_project(_NAME).shield_sets == ("python",)
    assert result.set_name == "python" and result.live


def test_a_built_in_set_that_does_not_name_the_target_is_refused() -> None:
    """Built-in sets are read-only: enabling one would grant everything but the target."""
    with _task([_entry(_HOST)]) as task:
        with pytest.raises(PromotionRefused, match="read-only"):
            promote_egress(_NAME, _TASK, _HOST, REMEDY_SET, set_name="python")
        assert task.path.read_text(encoding="utf-8") == _YML


def test_a_new_user_set_is_created_enabled_and_its_other_users_named() -> None:
    other = "proj-other"
    with _task([_entry(_HOST)]) as task:
        write_project(task.env.config_root, other, f"project:\n  name: {other}\n")
        _write_user_set(_USER_SET, "pypi.org\n")
        (task.env.config_root / other / "project.yml").write_text(
            f"project:\n  name: {other}\nshield:\n  sets: [{_USER_SET}]\n", encoding="utf-8"
        )
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_SET, set_name=_USER_SET)
        assert load_project(_NAME).shield_sets == (_USER_SET,)
    assert _user_set_path().read_text(encoding="utf-8") == f"pypi.org\n{_HOST}\n"
    assert other in result.message
    assert _user_set_path() in result.paths


def test_a_reserved_set_name_is_refused() -> None:
    with _task([_entry(_HOST)]):
        with pytest.raises(PromotionRefused, match="reserved"):
            promote_egress(_NAME, _TASK, _HOST, REMEDY_SET, set_name="none")


def test_a_set_without_a_target_is_enabled_for_everything_it_covers() -> None:
    """The suggestion's own command: one promotion, every refusal the set names."""
    entries = [_entry("pypi.org"), _entry("files.pythonhosted.org"), _entry(_HOST)]
    with _task(entries) as task:
        result = promote_egress(_NAME, _TASK, None, REMEDY_SET, set_name="python")
        assert load_project(_NAME).shield_sets == ("python",)
        assert task.manager.allow.call_count == 2
    assert result.target is None
    assert "pypi.org" in result.message


def test_a_set_without_a_target_that_covers_nothing_is_refused() -> None:
    with _task([_entry(_HOST)]) as task:
        with pytest.raises(PromotionRefused, match="covers nothing"):
            promote_egress(_NAME, _TASK, None, REMEDY_SET, set_name="python")
        with pytest.raises(PromotionRefused, match="no egress set is named"):
            promote_egress(_NAME, _TASK, None, REMEDY_SET, set_name="no-such-set")
        assert task.path.read_text(encoding="utf-8") == _YML


def test_a_set_never_covers_a_target_only_a_break_glass_reaches() -> None:
    """Enabling a set for what it covers counts only targets a set can lift."""
    with _task([_entry("pypi.org", standing="security")]):
        with pytest.raises(PromotionRefused, match="covers nothing"):
            promote_egress(_NAME, _TASK, None, REMEDY_SET, set_name="python")


# ── Destination: a break-glass override ────────────────────────────────


def test_an_override_is_written_and_opened_on_the_running_task() -> None:
    """Decision B: a vault-relayed host may be broken out, with a reason and an expiry."""
    with _task([_entry("api.vendor.example", standing="security")]) as task:
        result = promote_egress(
            _NAME,
            _TASK,
            "api.vendor.example",
            REMEDY_OVERRIDE,
            reason=_REASON,
            expires=_UNTIL,
        )
        task.manager.override.assert_called_once_with(
            _CONTAINER, "api.vendor.example", _UNTIL, reason=_REASON
        )
    assert result.live and result.next_start
    assert "kernel closes it" in result.message
    assert "through the vault" in result.message


def test_an_override_waits_for_a_stopped_task_to_start() -> None:
    with _task([_entry(PRIVATE_LAN_IP, standing="private")], running=False) as task:
        result = promote_egress(
            _NAME, _TASK, PRIVATE_LAN_IP, REMEDY_OVERRIDE, reason=_REASON, expires=_UNTIL
        )
        task.manager.override.assert_not_called()
        (override,) = load_project(_NAME).shield_override
    assert override.host == PRIVATE_LAN_IP
    assert not result.live
    assert "not running" in result.message


def test_an_override_the_running_task_cannot_take_still_holds_from_its_next_start() -> None:
    with _task([_entry(PRIVATE_LAN_IP, standing="private")]) as task:
        task.manager.override.side_effect = RuntimeError("no address")
        result = promote_egress(
            _NAME, _TASK, PRIVATE_LAN_IP, REMEDY_OVERRIDE, reason=_REASON, expires=_UNTIL
        )
    assert result.severity == "warning"
    assert "no address" in result.message


def test_an_override_whose_expiry_has_passed_is_refused_untouched() -> None:
    """Valid *through* the date, so today is the earliest — yesterday writes nothing."""
    yesterday = date.today() - timedelta(days=1)
    with _task([_entry(PRIVATE_LAN_IP, standing="private")]) as task:
        with pytest.raises(PromotionRefused, match="in the past"):
            promote_egress(
                _NAME,
                _TASK,
                PRIVATE_LAN_IP,
                REMEDY_OVERRIDE,
                reason=_REASON,
                expires=yesterday,
            )
        assert task.path.read_text(encoding="utf-8") == _YML
        task.manager.override.assert_not_called()


def test_a_result_round_trips_through_json() -> None:
    with _task([_entry(_HOST)]) as task:
        data = json.loads(json.dumps(promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT).as_dict()))
    assert data["destination"] == REMEDY_PROJECT
    assert data["paths"] == [str(task.path)]
    assert data["severity"] == "information"


# ── Export ─────────────────────────────────────────────────────────────


def _export(
    harvests: dict[str, Sequence[HarvestEntry]],
    *,
    yml: str = _YML,
    scrub: bool = False,
    tier: DnsTier | None = DnsTier.DNSMASQ_LIVE,
) -> dict[str, object]:
    """Export the project's tasks, each with its own harvest double."""
    managers = {task_id: _manager(entries, tier=tier) for task_id, entries in harvests.items()}
    with (
        project_env(yml, project_name=_NAME),
        patch(
            f"{_ENGINE}.task_shield_manager", side_effect=lambda task_dir: managers[task_dir.name]
        ),
        patch(f"{_ENGINE}.load_task_meta", return_value=({"mode": _MODE}, MOCK_BASE / "meta")),
    ):
        return export_egress(_NAME, list(harvests), scrub=scrub)


_PUBLIC = ALLOWED_TARGET_IPS[0]
"""A globally routable address, so the name behind it is no internal one."""

_HARVESTS: dict[str, Sequence[HarvestEntry]] = {
    "t1": [
        _entry("pypi.org", count=2, addresses=(_PUBLIC,), first=_FIRST, last=_FIRST),
        _entry(TEST_IP),
        _entry("nas.lan"),
        _entry("printer"),
    ],
    "t2": [
        _entry("PyPI.org", action="bypass", count=3, ports=(ALT_HTTPS_PORT,), last=_LAST),
        _entry("intranet.example.org", addresses=(PRIVATE_LAN_IP,)),
        _entry("files.example.org", standing="private"),
        _entry("api.vendor.example", standing="security"),
    ],
}


def test_export_aggregates_a_target_across_tasks() -> None:
    data = _export(_HARVESTS)
    assert (data["format"], data["version"]) == ("terok-egress-export", 1)
    assert data["project"] == _NAME and data["tasks"] == ["t1", "t2"]
    assert data["task_count"] == 2 and data["dns_tiers"] == ["dnsmasq-live"]
    pypi = next(e for e in data["entries"] if e["target"] == "pypi.org")  # type: ignore[union-attr]
    assert pypi["count"] == 5 and pypi["tasks"] == 2
    assert pypi["ports"] == [HTTPS_PORT, ALT_HTTPS_PORT]
    assert pypi["actions"] == ["blocked", "bypass"]
    assert (pypi["first_seen"], pypi["last_seen"]) == (_FIRST, _LAST)
    assert pypi["addresses"] == [_PUBLIC]
    assert [s["set"] for s in data["suggestions"]] == ["python"]  # type: ignore[union-attr]
    json.dumps(data)  # the document is plain JSON


def test_export_lists_every_tier_a_target_was_classified_under() -> None:
    harvests = {"t1": [_entry(_HOST)], "t2": [_entry(_HOST, standing="security")]}
    (entry,) = _export(harvests)["entries"]  # type: ignore[misc]
    assert entry["tier"] == TIER_SECURITY
    assert entry["tiers"] == [TIER_SECURITY, TIER_ALLOWLIST]


def test_a_scrubbed_export_drops_what_identifies_the_network() -> None:
    """Addresses, internal names, private destinations, ids, your own set names — all gone."""
    _write_user_set(_USER_SET, "api.vendor.example\n")
    yml = _YML + f"shield:\n  sets: [git-hosting, {_USER_SET}]\n"
    data = _export(_HARVESTS, yml=yml, scrub=True)
    assert data["scrubbed"] is True
    assert "project" not in data and "tasks" not in data
    targets = [e["target"] for e in data["entries"]]  # type: ignore[union-attr]
    assert targets == ["pypi.org", "api.vendor.example"]
    assert all("addresses" not in e for e in data["entries"])  # type: ignore[union-attr]
    assert data["scrubbed_out"] == {"addresses": 1, "internal_names": 4}
    assert data["enabled_sets"] == ["git-hosting"]
    assert _NAME not in json.dumps(data)


def test_a_scrubbed_export_suggests_built_in_sets_only() -> None:
    _write_user_set(_USER_SET, "pypi.org\nfiles.pythonhosted.org\nnodejs.org\n")
    harvests = {"t1": [_entry("pypi.org"), _entry("files.pythonhosted.org"), _entry("nodejs.org")]}
    plain = _export(harvests)
    scrubbed = _export(harvests, scrub=True)
    assert [s["set"] for s in plain["suggestions"]] == [_USER_SET]  # type: ignore[union-attr]
    assert [s["set"] for s in scrubbed["suggestions"]] == ["python", "node"]  # type: ignore[union-attr]


def test_an_export_from_a_tier_without_names_suggests_nothing() -> None:
    data = _export({"t1": [_entry("pypi.org")]}, tier=DnsTier.GETENT)
    assert data["domain_attribution"] is False
    assert data["suggestions"] == []


def test_export_counts_tasks_whose_audit_was_off() -> None:
    managers = {"t1": _manager([], audit=False)}
    with (
        project_env(_YML, project_name=_NAME),
        patch(f"{_ENGINE}.task_shield_manager", side_effect=lambda d: managers[d.name]),
        patch(f"{_ENGINE}.load_task_meta", return_value=({"mode": _MODE}, MOCK_BASE / "meta")),
    ):
        data = export_egress(_NAME, ["t1"])
    assert data["audit_off_tasks"] == 1 and data["entries"] == []


def test_export_without_task_ids_reads_every_task_that_ran_shielded() -> None:
    shielded, never = _manager([_entry(_HOST)]), _manager([])
    never.state_dir.is_dir.return_value = False
    managers = {"t1": shielded, "t2": never}
    with (
        project_env(_YML, project_name=_NAME),
        patch(f"{_ENGINE}.iter_task_ids", return_value=iter(["t2", "t1"])),
        patch(f"{_ENGINE}.task_shield_manager", side_effect=lambda d: managers[d.name]),
    ):
        data = export_egress(_NAME)
    assert data["tasks"] == ["t1"]


def test_export_of_an_unknown_task_fails_loudly() -> None:
    with (
        project_env(_YML, project_name=_NAME),
        patch(f"{_ENGINE}.load_task_meta", side_effect=SystemExit("Unknown task zz")),
        pytest.raises(SystemExit, match="Unknown task"),
    ):
        export_egress(_NAME, ["zz"])


# ── Edges of the engine ────────────────────────────────────────────────

_NEVER_STARTED = ({}, MOCK_BASE / "meta")
"""Task metadata with no mode: the task has never run, so it has no container."""


def test_a_refusal_names_an_unknown_destination_and_an_unknown_tier() -> None:
    """Text from a newer caller or a newer shield still gets a reason, never a silent ``None``."""
    future = HarvestEntry(
        action="blocked",
        target=_HOST,
        count=1,
        first_seen=_FIRST,
        last_seen=_LAST,
        ports=(HTTPS_PORT,),
        addresses=(),
        tier="t15_future",
        remedies=(REMEDY_TASK,),
    )
    with _task([future]):
        candidate = review_egress(_NAME, _TASK).candidate(_HOST)
    assert candidate is not None
    assert "'nowhere' is no destination" in (candidate.refusal("nowhere") or "")
    assert candidate.refusal(REMEDY_PROJECT) == (
        f"the shield classifies {_HOST} as t15_future, and offers {REMEDY_TASK} for it"
    )


def test_a_task_that_never_started_has_nothing_to_allow_on() -> None:
    with (
        _task([_entry(_HOST)]) as task,
        patch(f"{_ENGINE}.load_task_meta", return_value=_NEVER_STARTED),
        pytest.raises(PromotionRefused, match="has never started"),
    ):
        promote_egress(_NAME, _TASK, _HOST, REMEDY_TASK)
    task.manager.allow.assert_not_called()


def test_a_project_promotion_for_a_task_that_never_started_is_written_only() -> None:
    with (
        _task([_entry(_HOST)]) as task,
        patch(f"{_ENGINE}.load_task_meta", return_value=_NEVER_STARTED),
    ):
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        assert load_project(_NAME).shield_allow == (_HOST,)
    task.manager.allow.assert_not_called()
    assert not result.live


def test_a_task_whose_state_cannot_be_read_counts_as_stopped() -> None:
    """A failed probe never applies a grant live: the project file carries it to the next start."""
    with (
        _task([_entry(_HOST)]) as task,
        patch(f"{_ENGINE}.get_task_container_state", side_effect=RuntimeError("podman gone")),
    ):
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
    task.manager.allow.assert_not_called()
    assert not result.live


def test_enabling_a_user_set_that_does_not_load_is_refused_untouched() -> None:
    """The set is the operator's to fix; enabling it would only fail every task start."""
    _write_user_set(_USER_SET, "not a host at all!\n")
    with _task([_entry(_HOST)]) as task:
        with pytest.raises(PromotionRefused, match="does not load"):
            promote_egress(_NAME, _TASK, None, REMEDY_SET, set_name=_USER_SET)
        assert task.path.read_text(encoding="utf-8") == _YML


def test_a_project_promotion_the_security_tier_refuses_live_still_holds_in_the_file() -> None:
    """Lifting the task's own deny can meet a shared vault address — the project grant stands."""
    with _task([_entry(_HOST, standing="operator-deny")]) as task:
        task.manager.allow.side_effect = SecurityTierRefusal(
            _HOST, entry="api.vendor.example", address=TEST_IP
        )
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        assert load_project(_NAME).shield_allow == (_HOST,)
    assert result.severity == "warning"


def test_a_live_project_grant_that_took_no_address_warns() -> None:
    with _task([_entry(_HOST)]) as task:
        task.manager.allow.side_effect = lambda _container, target: AllowResult(
            target=target, ips=(), live=True
        )
        result = promote_egress(_NAME, _TASK, _HOST, REMEDY_PROJECT)
        assert load_project(_NAME).shield_allow == (_HOST,)
    assert result.severity == "warning"


def test_a_scrubbed_export_ignores_an_address_it_cannot_read() -> None:
    """An unparseable address proves nothing internal about its name, so the name is kept."""
    data = _export({"t1": [_entry("cdn.example.org", addresses=("not-an-address",))]}, scrub=True)
    assert [e["target"] for e in data["entries"]] == ["cdn.example.org"]  # type: ignore[union-attr]
