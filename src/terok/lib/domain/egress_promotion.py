# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Promotion: turning what a task reached for into policy, one explicit grant at a time.

A deny-by-default shield is workable only if an operator can see what it
refused and move what was really needed into policy.  The shield records the
first half — a task's harvest — and this module is the second: it reviews a
harvest and applies one promotion at a time, for the CLI and the TUI alike, so
both surfaces offer the same choices and say the same things about them.

Tier authority decides, never this module's opinion of a host.  Shield
classifies every harvested target by *destination* — which tier stands between
the task and it, and which remedies could lift that — and the review offers
exactly those remedies as destinations:

- **task** — allow it for this task: live while the container runs, recorded
  in the task's runtime overlay for its next start when it is stopped;
- **project** — add it to the project's ``shield.allow``, for every task from
  its next start;
- **set** — enable a built-in egress set that names it, or add it to one of
  the operator's own sets (created when new) and enable that;
- **override** — a break-glass ``shield.override`` above the security tier,
  with a reason and an expiry, both required.

So a target nothing allows yet (``t40``) goes to the task, the project or a
set; one the operator's own runtime deny holds goes to the task or the
project; one the security tier refuses — a vault-relayed provider host above
all — or one in a private range takes an override or nothing; and the
hard-deny floor (link-local, cloud metadata, the host's own loopback) takes
nothing at all.  An override for a vault-relayed host gives direct reach
only: agents keep relaying their keys through the vault.

Every promotion takes its target from the task's own harvest, which keeps the
verb a review of evidence rather than a general allowlist editor.  A project,
set or override promotion also reaches the task it came from: live when that
task runs, so the operator sees the effect at once — and when the target sits
behind the task's own runtime deny, by lifting that deny, live or for its next
start, since no project grant reaches past it.  Each result says what took
effect now and what waits for a next start.

The review also suggests egress sets — a greedy cover of the targets nothing
allows yet, by the sets the project does not enable: the set covering the most
first, ties to the smaller set, then built-in before the operator's own.  A
set names hosts, so it covers a target by exact name — or by suffix on the
``dnsmasq-live`` DNS tier, where a listed domain admits its subdomains.  On a
tier without dnsmasq the shield records addresses only, so there are no names
to match: the review says so and suggests nothing.

[`export_egress`][terok.lib.domain.egress_promotion.export_egress] aggregates
the harvests of a project's tasks into the JSON user groups send back to
improve the built-in sets, optionally scrubbed of anything that identifies
the network it came from.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from terok import __version__
from terok.lib.integrations.sandbox import (
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
    SecurityTierRefusal,
)

from ..core.egress_sets import (
    BUILT_IN_ORIGIN,
    EGRESS_SETS,
    RECOMMENDED_SET,
    EgressEdit,
    add_host_to_user_set,
    egress_set_catalog,
    egress_set_hosts,
    resolve_egress_sets,
    selected_egress_sets,
)
from ..core.projects import (
    add_project_shield_allow,
    add_project_shield_override,
    egress_set_users,
    enable_project_shield_set,
    load_project,
)
from ..orchestration.task_runners import task_shield_manager
from ..orchestration.tasks import (
    container_name,
    get_task_container_state,
    iter_task_ids,
    load_task_meta,
    tasks_meta_dir,
)
from ..util.net import git_remote_host

if TYPE_CHECKING:
    from terok.lib.integrations.sandbox import (
        DnsTier,
        HarvestEntry,
        HarvestReport,
        ShieldManager,
    )

    from ..core.project_model import ProjectConfig

PROMOTION_DESTINATIONS: tuple[str, ...] = (REMEDY_TASK, REMEDY_PROJECT, REMEDY_SET, REMEDY_OVERRIDE)
"""Where a promotion can put a target, in preference order — shield's remedy vocabulary.

``host_port`` is a remedy too, but no promotion: the host's own loopback is
reached through a per-port ``+localhost:PORT`` grant, one port at a time.
"""

PromotionSeverity = Literal["information", "warning", "error"]
"""How loudly a [`PromotionResult`][terok.lib.domain.egress_promotion.PromotionResult] should be shown."""

EXPORT_FORMAT = "terok-egress-export"
"""The ``format`` an export names itself by, so a receiver can tell it from other JSON."""

EXPORT_VERSION = 1
"""The export format's version; bumped when a field changes meaning or goes away."""

_STRICTNESS: tuple[str, ...] = (
    TIER_HARD_DENY,
    TIER_SECURITY,
    TIER_PRIVATE,
    TIER_OPERATOR_DENY,
    TIER_ALLOWLIST,
)
"""The tiers, most restrictive first.  A tier this terok does not know ranks
above all of them: what it cannot place, it offers nothing more for."""

_REVIEW_ORDER: tuple[str, ...] = (
    TIER_ALLOWLIST,
    TIER_OPERATOR_DENY,
    TIER_SECURITY,
    TIER_PRIVATE,
    TIER_HARD_DENY,
)
"""How a review lists the tiers: what can be promoted outright first, break-glass
next, the floor last — the shield's own harvest order."""

_TIER_HEADINGS: dict[str, str] = {
    TIER_ALLOWLIST: "Not on the allowlist — promote to this task, the project, or a set",
    TIER_OPERATOR_DENY: "Held by your own runtime deny — lift it for this task or the project",
    TIER_SECURITY: (
        "Refused by the security tier — break-glass only, with a reason and an expiry "
        "(agents keep relaying keys through the vault)"
    ),
    TIER_PRIVATE: "In a private network range — break-glass only, with a reason and an expiry",
    TIER_HARD_DENY: (
        "Hard-deny floor (link-local, cloud metadata, the host's own loopback) — never promoted"
    ),
}

_INTERNAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".corp")
"""Name suffixes that only mean something inside one network, scrubbed from an export."""


class PromotionRefused(ValueError):
    """A promotion that was not attempted: the request itself is not allowed.

    The target is not in the task's harvest, the tier that refused it allows
    no such destination, a set does not name it, or an override lacks its
    reason or expiry.  Nothing was changed, and the message names the route
    that is open, when there is one.
    """


# ── Review ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EgressCandidate:
    """One target a task reached for — every harvest line about it merged — and what may be done.

    The harvest keys its lines by action and target; a promotion is a
    decision about the target, so its lines are merged here: counts summed,
    ports and addresses joined, the first and last sighting kept.  When the
    lines disagree about the tier, the most restrictive wins, with the
    remedies all of them allow — a remedy has to work for every address the
    target was seen at.
    """

    target: str
    """The domain the shield recovered, or the address when it had none."""
    tier: str
    """The tier standing between the task and the target (a ``TIER_*`` value)."""
    remedies: tuple[str, ...]
    """What could lift that tier, in preference order (``REMEDY_*`` values)."""
    actions: tuple[str, ...]
    """What the shield did about it — ``blocked``, ``bypass``, ``down``, …"""
    count: int
    """Audit lines about it: a floor on attempts, not a connection count."""
    first_seen: str
    last_seen: str
    ports: tuple[int, ...]
    """Destination ports, ascending."""
    addresses: tuple[str, ...]
    """Addresses seen behind a domain target; empty for an address target."""
    covered_by: str | None = None
    """What in the project already grants it — the egress set, ``shield.allow``,
    the git upstream host or ``shield.override`` — or ``None``.  A refusal of a
    granted target means the grant came after the task started (it applies from
    the task's next start), or, on a DNS tier that resolves names once, that
    the name moved to an address the launch did not pin."""
    suggested_set: str | None = None
    """The suggested egress set that covers it, when one does."""

    @property
    def destinations(self) -> tuple[str, ...]:
        """The promotions the tier allows for this target, in preference order."""
        return tuple(remedy for remedy in self.remedies if remedy in PROMOTION_DESTINATIONS)

    @property
    def is_address(self) -> bool:
        """Whether the target is an address or a range rather than a name."""
        return _as_network(self.target) is not None

    def refusal(self, destination: str) -> str | None:
        """Why *destination* is not open for this target, or ``None`` when it is."""
        if destination in self.destinations:
            return None
        target = self.target
        if destination not in PROMOTION_DESTINATIONS:
            return (
                f"{destination!r} is no destination: promote to {', '.join(PROMOTION_DESTINATIONS)}"
            )
        if self.tier == TIER_HARD_DENY:
            if REMEDY_HOST_PORT in self.remedies:
                return (
                    f"{target} is the host's own loopback: only a per-port +localhost:PORT "
                    "grant reaches it, never a promotion"
                )
            return (
                f"{target} is in the hard-deny floor (link-local, cloud metadata): nothing "
                "reaches it, not even a break-glass override"
            )
        if self.tier in (TIER_SECURITY, TIER_PRIVATE):
            where = (
                "is refused by the security tier"
                if self.tier == TIER_SECURITY
                else "is in a private network range"
            )
            return (
                f"{target} {where}: only a break-glass override reaches it, with a reason "
                "and an expiry"
            )
        if self.tier == TIER_OPERATOR_DENY:
            return (
                f"{target} is held by your own runtime deny: lift it for this task or the project"
            )
        if self.tier == TIER_ALLOWLIST and destination == REMEDY_OVERRIDE:
            return (
                f"{target} is refused only because nothing allows it: a break-glass override "
                "would lift it above the security tier for no reason — allow it for the task, "
                "the project or a set"
            )
        offered = ", ".join(self.destinations) or "nothing"
        return f"the shield classifies {target} as {self.tier}, and offers {offered} for it"

    def as_dict(self) -> dict[str, Any]:
        """The candidate as JSON-ready data, with every closed destination's reason."""
        return {
            "target": self.target,
            "tier": self.tier,
            "remedies": list(self.remedies),
            "destinations": list(self.destinations),
            "refusals": {
                destination: reason
                for destination in PROMOTION_DESTINATIONS
                if (reason := self.refusal(destination)) is not None
            },
            "actions": list(self.actions),
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "ports": list(self.ports),
            "addresses": list(self.addresses),
            "covered_by": self.covered_by,
            "suggested_set": self.suggested_set,
        }


@dataclass(frozen=True, slots=True)
class SetSuggestion:
    """An egress set the project does not enable that would cover targets nothing allows yet."""

    name: str
    origin: str
    """``built-in`` or ``user`` — where the set comes from."""
    covers: tuple[str, ...]
    """The targets this suggestion covers that no earlier suggestion did."""
    of: int
    """How many targets nothing allows yet — what all the suggestions draw from."""
    hosts: int
    """How many hosts enabling the set grants — what else comes with it."""

    @property
    def summary(self) -> str:
        """One line: the set, how much of what is missing it covers, and what it grants."""
        noun = "host" if self.hosts == 1 else "hosts"
        return (
            f"{self.name} ({self.origin}, {self.hosts} {noun}) covers {len(self.covers)} of "
            f"{self.of} targets nothing allows yet: {', '.join(self.covers)}"
        )

    def as_dict(self) -> dict[str, Any]:
        """The suggestion as JSON-ready data."""
        return {
            "set": self.name,
            "origin": self.origin,
            "covers": list(self.covers),
            "of": self.of,
            "hosts": self.hosts,
        }


@dataclass(frozen=True, slots=True)
class EgressReview:
    """What one task reached for, merged per target and ready to promote."""

    project: str
    task: str
    candidates: tuple[EgressCandidate, ...]
    """One per target, in review order: by tier — promotable outright first,
    break-glass next, the floor last — then loudest first."""
    suggestions: tuple[SetSuggestion, ...]
    """Egress sets that would cover what nothing allows yet, best first."""
    audit_enabled: bool
    """Whether the task was created with shield audit on.  With it off nothing
    was recorded, so an empty review says nothing about the task."""
    dns_tier: str | None
    """The DNS tier the task launched with, or ``None`` when none was recorded."""
    domain_attribution: bool
    """Whether the tier names what it refuses.  Without dnsmasq the shield
    records addresses only, so no set can be matched to them."""
    suffix_match: bool = False
    """Whether a listed domain admits its subdomains — true on ``dnsmasq-live``."""

    def candidate(self, target: str) -> EgressCandidate | None:
        """The candidate for *target* (names compare case-insensitively), or ``None``."""
        wanted = _normalized(target)
        return next((c for c in self.candidates if _normalized(c.target) == wanted), None)

    @property
    def empty_reason(self) -> str | None:
        """Why there is nothing to review, worded for the operator; ``None`` when there is something."""
        if self.candidates:
            return None
        if not self.audit_enabled:
            return (
                "Nothing harvested — and nothing could be: shield audit was off when this "
                "task was created, so its refusals were never recorded.  An empty review "
                "here is no all-clear."
            )
        return (
            "Nothing harvested: the task was refused nothing, and neither a bypass window "
            "nor the DOWN posture let through anything the allowlist would refuse."
        )

    @property
    def attribution_note(self) -> str | None:
        """Why no set is suggested on this DNS tier, or ``None`` when names are recorded."""
        if self.domain_attribution:
            return None
        return (
            f"No domain attribution on the {self.dns_tier} DNS tier: without dnsmasq the "
            "shield records addresses only, so no egress set can be suggested, and an "
            "address allowed today stops matching when its name moves.  Install dnsmasq "
            "on the host: tasks launched after that record names."
        )

    def as_dict(self) -> dict[str, Any]:
        """The review as JSON-ready data."""
        return {
            "project": self.project,
            "task": self.task,
            "dns_tier": self.dns_tier,
            "domain_attribution": self.domain_attribution,
            "audit_enabled": self.audit_enabled,
            "suggestions": [suggestion.as_dict() for suggestion in self.suggestions],
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


def tier_heading(tier: str) -> str:
    """The heading a review groups *tier*'s candidates under — one wording, every surface."""
    return _TIER_HEADINGS.get(tier, f"Classified {tier} by the shield — nothing to offer")


def review_egress(project_name: str, task_id: str) -> EgressReview:
    """Review what task *task_id* of *project_name* reached for, ready to promote.

    Reads the task's harvest and recorded DNS tier — files only, no
    container is touched — and merges, classifies and annotates them: which
    destinations each target may go to and why the others are closed, what
    in the project already grants a target, and which egress sets would
    cover what nothing allows yet.  The audit log is read whole, so keep
    this off a UI thread.

    Raises:
        SystemExit: The project does not load, or the task is unknown.
    """
    task = _TaskContext.load(project_name, task_id)
    return _review(task.project, task.task_id, task.manager.harvest(), task.manager.dns_tier)


# ── Promotion ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromotionResult:
    """What one promotion did: what took effect now, and what waits for a next start."""

    target: str | None
    """The promoted target; ``None`` when a set was enabled for what it covers."""
    destination: str
    set_name: str | None = None
    notes: tuple[str, ...] = ()
    """What happened, line by line."""
    warnings: tuple[str, ...] = ()
    """What the operator must hear: a part that did not apply, a grant that will decay."""
    paths: tuple[Path, ...] = ()
    """The files the promotion changed — ``project.yml``, a user set."""
    live: bool = False
    """Whether the running task took the grant now."""
    next_start: bool = False
    """Whether (some of) the grant applies from a next start — this task's or every task's."""
    failed: bool = False
    """Whether nothing was granted at all."""

    @property
    def severity(self) -> PromotionSeverity:
        """``error`` when nothing was granted, ``warning`` when part of it needs attention."""
        if self.failed:
            return "error"
        return "warning" if self.warnings else "information"

    @property
    def message(self) -> str:
        """Every line, notes first — for a surface that shows one block of text."""
        return "\n".join((*self.notes, *self.warnings))

    def as_dict(self) -> dict[str, Any]:
        """The result as JSON-ready data."""
        return {
            "target": self.target,
            "destination": self.destination,
            "set": self.set_name,
            "severity": self.severity,
            "live": self.live,
            "next_start": self.next_start,
            "paths": [str(path) for path in self.paths],
            "notes": list(self.notes),
            "warnings": list(self.warnings),
        }


def promote_egress(
    project_name: str,
    task_id: str,
    target: str | None,
    destination: str,
    *,
    set_name: str | None = None,
    reason: str | None = None,
    expires: date | None = None,
) -> PromotionResult:
    """Promote *target* from the task's harvest to *destination*, and say what that did.

    *destination* is one of
    [`PROMOTION_DESTINATIONS`][terok.lib.domain.egress_promotion.PROMOTION_DESTINATIONS],
    and it must be one the tier standing in the target's way allows
    ([`EgressCandidate.refusal`][terok.lib.domain.egress_promotion.EgressCandidate.refusal]
    says why when it is not):

    - ``task`` allows it for this task — live while the container runs, from
      its next start when it is stopped.
    - ``project`` adds it to ``shield.allow``; ``set`` enables *set_name* —
      a built-in set that names the target, or one of the operator's own,
      which gets the target added (and the file created when it is new).
      Without a *target*, ``set`` enables *set_name* for whatever it covers in
      the harvest — refused when that is nothing.
    - ``override`` adds a break-glass ``shield.override`` with *reason* and
      *expires* (valid through that day), both required and meaningless for
      every other destination.

    A project, set or override promotion also reaches this task: live while
    it runs, and — for a target its own runtime deny holds — by lifting that
    deny, which no project grant gets past.  Blocking (it may write files and
    enter the container's network namespace): call it from a worker thread.

    Raises:
        PromotionRefused: The request is not allowed, and nothing changed.
        SystemExit: The project does not load, the task is unknown, or a
            file the operator owns (``project.yml``, a user set) needs fixing.
        RuntimeError: The shield could not take a grant on this task alone —
            the kill switch is set, or the task's bundle was never prepared.
        OSError: A file could not be written.
    """
    _check_request(target, destination, set_name=set_name, reason=reason, expires=expires)
    task = _TaskContext.load(project_name, task_id)
    review = _review(task.project, task.task_id, task.manager.harvest(), task.manager.dns_tier)
    if target is None:
        return _enable_covering_set(task, review, str(set_name))
    candidate = review.candidate(target)
    if candidate is None:
        raise PromotionRefused(
            f"{target} is not among what task {task.label} reached for: a promotion takes its "
            "target from the task's harvest — review the task to see what it holds"
        )
    if (why := candidate.refusal(destination)) is not None:
        raise PromotionRefused(why)
    if destination == REMEDY_TASK:
        return _promote_to_task(task, candidate, review)
    if destination == REMEDY_PROJECT:
        return _promote_to_project(task, candidate)
    if destination == REMEDY_SET:
        return _promote_to_set(task, candidate, review, str(set_name))
    return _promote_to_override(task, candidate, str(reason).strip(), _required(expires))


def _required(expires: date | None) -> date:
    """*expires*, which ``_check_request`` has vouched for on an override."""
    if expires is None:  # pragma: no cover - refused before any file is read
        raise PromotionRefused("a break-glass override needs an expiry")
    return expires


def _check_request(
    target: str | None,
    destination: str,
    *,
    set_name: str | None,
    reason: str | None,
    expires: date | None,
) -> None:
    """Refuse a request whose arguments do not fit its destination, before anything is read."""
    if destination not in PROMOTION_DESTINATIONS:
        raise PromotionRefused(
            f"{destination!r} is no destination: promote to {', '.join(PROMOTION_DESTINATIONS)}"
        )
    if (set_name is not None) != (destination == REMEDY_SET):
        raise PromotionRefused("a set name goes with a promotion to a set, and only there")
    if target is None and destination != REMEDY_SET:
        raise PromotionRefused(f"a promotion to {destination} needs a target")
    if destination == REMEDY_OVERRIDE:
        if not (reason or "").strip() or expires is None:
            raise PromotionRefused(
                "a break-glass override needs both a reason (the audit trail for the hole it "
                "opens) and an expiry (the day it closes)"
            )
    elif reason is not None or expires is not None:
        raise PromotionRefused("a reason and an expiry belong to a break-glass override only")


def _promote_to_task(
    task: _TaskContext, candidate: EgressCandidate, review: EgressReview
) -> PromotionResult:
    """Allow the target for this task alone: live, or from its next start when it is stopped."""
    target = candidate.target
    if task.container is None:
        raise PromotionRefused(f"task {task.label} has never started: there is nothing to allow on")
    try:
        granted = task.manager.allow(task.container, target)
    except SecurityTierRefusal as refusal:
        raise PromotionRefused(
            f"{refusal.cause}: only a break-glass override, with a reason and an expiry, gets "
            "past the security tier"
        ) from refusal
    lifted = (
        " — its own runtime deny of it is lifted" if candidate.tier == TIER_OPERATOR_DENY else ""
    )
    if not granted.live:
        return PromotionResult(
            target,
            REMEDY_TASK,
            notes=(
                f"Allowed {target} for task {task.label} from its next start{lifted}: it is "
                "not running, so the grant waits in its runtime overlay",
            ),
            next_start=True,
        )
    if not granted.ips:
        return PromotionResult(
            target,
            REMEDY_TASK,
            warnings=(
                f"The running task {task.label} took none of {target}'s addresses — nothing "
                "was allowed; the shield's log says why",
            ),
            failed=True,
        )
    return PromotionResult(
        target,
        REMEDY_TASK,
        notes=(
            f"Allowed {target} for task {task.label} now ({', '.join(granted.ips)}){lifted}; "
            "it stays with that task across its restarts",
        ),
        warnings=_address_only_warning(candidate, review),
        live=True,
    )


def _promote_to_project(task: _TaskContext, candidate: EgressCandidate) -> PromotionResult:
    """Add the target to ``shield.allow``, then bring it to the task it came from."""
    edit = _write(add_project_shield_allow, task.project.name, candidate.target)
    applied = _apply_to_task(task, candidate)
    return PromotionResult(
        candidate.target,
        REMEDY_PROJECT,
        notes=(
            edit.message,
            f"Every task of {task.project.name} gets it from its next start.",
            *applied.notes,
        ),
        warnings=applied.warnings,
        paths=_changed(edit),
        live=applied.live,
        next_start=True,
    )


def _promote_to_set(
    task: _TaskContext, candidate: EgressCandidate, review: EgressReview, name: str
) -> PromotionResult:
    """Enable a set that names the target — adding it first when the set is the operator's own."""
    target = candidate.target
    notes: list[str] = []
    edits: list[EgressEdit] = []
    if _is_built_in(name):
        if not _set_covers(task.project, name, target, suffix=review.suffix_match):
            raise PromotionRefused(
                f"{target} is not in the built-in set {name!r}, and built-in sets are "
                "read-only: add it to one of your own sets, or allow it for the project"
            )
    else:
        added = _write(add_host_to_user_set, name, target)
        edits.append(added)
        notes.append(added.message)
        if added.changed:
            notes += _other_users(name, task.project.name, target)
    enabled = _write(enable_project_shield_set, task.project.name, name)
    edits.append(enabled)
    applied = _apply_to_task(task, candidate)
    return PromotionResult(
        target,
        REMEDY_SET,
        set_name=name,
        notes=(
            *notes,
            enabled.message,
            f"Every task of {task.project.name} gets it from its next start.",
            *applied.notes,
        ),
        warnings=applied.warnings,
        paths=_changed(*edits),
        live=applied.live,
        next_start=True,
    )


def _enable_covering_set(task: _TaskContext, review: EgressReview, name: str) -> PromotionResult:
    """Enable set *name* for the targets nothing allows yet that it covers — refused when none."""
    known = {egress_set.name: egress_set for egress_set in egress_set_catalog()}
    if name not in known and name != RECOMMENDED_SET:
        raise PromotionRefused(
            f"no egress set is named {name!r} — promote a target into it to create it"
        )
    if (broken := known.get(name)) is not None and broken.error is not None:
        raise PromotionRefused(
            f"the egress set {name!r} does not load ({broken.path}): {broken.error}"
        )
    hosts = resolve_egress_sets((name,), task.project.known_family)
    covered = [
        c
        for c in review.candidates
        if REMEDY_SET in c.remedies
        and any(_covers(host, c.target, suffix=review.suffix_match) for host in hosts)
    ]
    if not covered:
        raise PromotionRefused(
            f"the egress set {name!r} covers nothing task {task.label} was refused: enabling it "
            "would only widen the project — edit its shield.sets if that is what you want"
        )
    enabled = _write(enable_project_shield_set, task.project.name, name)
    applied = [_apply_to_task(task, candidate) for candidate in covered]
    return PromotionResult(
        None,
        REMEDY_SET,
        set_name=name,
        notes=(
            enabled.message,
            f"It covers {', '.join(c.target for c in covered)}; every task of "
            f"{task.project.name} gets the set from its next start.",
            *(note for one in applied for note in one.notes),
        ),
        warnings=tuple(warning for one in applied for warning in one.warnings),
        paths=_changed(enabled),
        live=any(one.live for one in applied),
        next_start=True,
    )


def _promote_to_override(
    task: _TaskContext, candidate: EgressCandidate, reason: str, expires: date
) -> PromotionResult:
    """Write the break-glass override, then open it on the task it came from when that runs."""
    target = candidate.target
    edit = _write(add_project_shield_override, task.project.name, target, reason, expires)
    until = f"until the end of {expires.isoformat()}, host local time"
    notes = [edit.message]
    warnings: list[str] = []
    live = False
    if task.container is not None and task.is_running:
        try:
            opened = task.manager.override(task.container, target, expires, reason=reason)
        except (ValueError, RuntimeError) as exc:
            warnings.append(
                f"Not opened on the running task {task.label} now: {exc}.  The project's "
                "override reaches it at its next start."
            )
        else:
            live = True
            notes.append(
                f"Opened on the running task {task.label} now ({', '.join(opened)}), {until}: "
                "the kernel closes it then."
            )
    else:
        notes.append(
            f"Task {task.label} is not running: the override reaches it at its next start."
        )
    notes.append(f"Every task of {task.project.name} gets it from its next start, {until}.")
    if candidate.tier == TIER_SECURITY:
        notes.append(
            "Agents keep relaying keys through the vault; the override only adds the direct route."
        )
    return PromotionResult(
        target,
        REMEDY_OVERRIDE,
        notes=tuple(notes),
        warnings=tuple(warnings),
        paths=_changed(edit),
        live=live,
        next_start=True,
    )


@dataclass(frozen=True, slots=True)
class _Applied:
    """What bringing a project-wide grant to the originating task did there."""

    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    live: bool = False


def _apply_to_task(task: _TaskContext, candidate: EgressCandidate) -> _Applied:
    """Bring a project-wide grant to the task it was promoted from, where that helps or is needed.

    A running task takes it live, so the operator sees the effect at once
    (the grant joins that task's runtime overlay and stays with it).  A
    stopped task needs nothing — it starts with the project's grant — unless
    its own runtime deny holds the target: that deny sits above any project
    grant, so it is lifted in the task's overlay for its next start.
    """
    target = candidate.target
    lift = candidate.tier == TIER_OPERATOR_DENY
    if task.container is None:
        return _Applied()
    if not lift and not task.is_running:
        return _Applied(
            notes=(f"Task {task.label} is not running: it picks it up at its next start.",)
        )
    try:
        granted = task.manager.allow(task.container, target)
    except SecurityTierRefusal as refusal:
        return _Applied(
            warnings=(
                f"Not applied to task {task.label}: {refusal.cause} — only a break-glass "
                "override gets past the security tier.",
            )
        )
    except RuntimeError as exc:
        return _Applied(
            warnings=(
                f"Not applied to task {task.label} now ({exc}); it picks it up at its next start.",
            )
        )
    if not granted.live:
        return _Applied(
            notes=(f"Lifted task {task.label}'s own deny of {target} from its next start.",)
        )
    if not granted.ips:
        return _Applied(
            warnings=(
                f"The running task {task.label} took none of {target}'s addresses; it picks "
                "the grant up at its next start.",
            )
        )
    lifted = " — its own deny of it is lifted" if lift else ""
    return _Applied(
        notes=(
            f"Applied to the running task {task.label} now ({', '.join(granted.ips)}){lifted}; "
            "it stays in that task's runtime overlay.",
        ),
        live=True,
    )


def _address_only_warning(candidate: EgressCandidate, review: EgressReview) -> tuple[str, ...]:
    """The warning an address grant carries on a tier that names nothing — empty otherwise."""
    if review.domain_attribution or not candidate.is_address:
        return ()
    return (
        f"{candidate.target} is an address: the {review.dns_tier} DNS tier records no names, "
        "so if it belongs to a name that moves, the next address is refused again.",
    )


def _other_users(name: str, project_name: str, target: str) -> list[str]:
    """The note naming the other projects an edit to user set *name* widens, if any."""
    others = [user for user in egress_set_users().get(name, ()) if user != project_name]
    if not others:
        return []
    return [
        f"The set {name!r} is enabled by {', '.join(others)} too: their tasks get {target} "
        "from their next start as well."
    ]


def _write(writer: Callable[..., EgressEdit], *args: Any) -> EgressEdit:
    """Run one policy writer; its ``ValueError`` (bad input, nothing written) is a refusal."""
    try:
        return writer(*args)
    except ValueError as exc:
        raise PromotionRefused(str(exc)) from exc


def _changed(*edits: EgressEdit) -> tuple[Path, ...]:
    """The files the *edits* actually changed, each once."""
    return tuple(dict.fromkeys(edit.path for edit in edits if edit.changed))


# ── Export ─────────────────────────────────────────────────────────────


def export_egress(
    project_name: str, task_ids: Sequence[str] | None = None, *, scrub: bool = False
) -> dict[str, Any]:
    """Aggregate what the tasks of *project_name* reached for, as the JSON a user group sends back.

    Every task that ran shielded counts, or just *task_ids*.  Entries are
    merged per target across tasks — attempts summed, first and last
    sighting kept, ports and addresses joined — and carry the tiers they were
    classified under (most restrictive first) and how many tasks saw them.
    Set suggestions are computed over the merged entries against the
    project's current selection, as a review would.

    *scrub* makes the export safe to share outside the network it came from:
    address targets and every ``addresses`` list go, as do names that only
    mean something inside one network — single-label names, ``.local``,
    ``.lan``, ``.internal``, ``.home.arpa`` and ``.corp`` names, names refused
    as private or link-local destinations, and names seen only at addresses
    that are not globally routable — along with the project's name and its
    task ids.  The operator's own set names go too: only built-in sets are
    suggested or listed as enabled, and those mean the same on every host.
    How many entries were dropped, and why, is kept.

    The format is documented in the shield security guide; ``format`` and
    ``version`` identify it.

    Raises:
        SystemExit: The project does not load, or a named task is unknown.
    """
    project = load_project(project_name)
    if task_ids is None:
        ids = _shielded_task_ids(project)
    else:
        ids = [str(task_id) for task_id in task_ids]
        for task_id in ids:
            load_task_meta(project.name, task_id)  # an unknown task fails loudly
    harvests: list[tuple[str, HarvestReport, DnsTier | None]] = []
    for task_id in ids:
        manager = task_shield_manager(project.tasks_root / task_id)
        harvests.append((task_id, manager.harvest(), manager.dns_tier))
    return _export(project, harvests, scrub=scrub)


def _shielded_task_ids(project: ProjectConfig) -> list[str]:
    """The project's tasks that ran shielded at least once — the ones with a harvest to read."""
    return sorted(
        task_id
        for task_id in iter_task_ids(tasks_meta_dir(project.name))
        if task_shield_manager(project.tasks_root / task_id).state_dir.is_dir()
    )


def _export(
    project: ProjectConfig,
    harvests: Sequence[tuple[str, HarvestReport, DnsTier | None]],
    *,
    scrub: bool,
) -> dict[str, Any]:
    """Build the export document from each task's harvest and DNS tier."""
    seen_by: dict[str, set[str]] = {}
    tiers_of: dict[str, set[str]] = {}
    entries: list[HarvestEntry] = []
    for task_id, report, _tier in harvests:
        for entry in report:
            key = _normalized(entry.target)
            seen_by.setdefault(key, set()).add(task_id)
            tiers_of.setdefault(key, set()).add(entry.tier)
            entries.append(entry)
    dns_tiers = [tier for _, _, tier in harvests]
    attribution = not dns_tiers or any(t is None or t.runs_dnsmasq for t in dns_tiers)
    suffix = bool(dns_tiers) and all(t is not None and t.live for t in dns_tiers)
    grants = _Grants.of(project)
    candidates = [
        replace(c, covered_by=grants.granted_by(c, suffix=suffix)) for c in _merge(entries)
    ]
    dropped = {"addresses": 0, "internal_names": 0}
    if scrub:
        candidates, dropped = _scrubbed(candidates)
    suggestions = (
        _suggest_sets(candidates, project, suffix=suffix, user_sets=not scrub)
        if attribution
        else ()
    )
    enabled = selected_egress_sets(project.shield_sets)
    document: dict[str, Any] = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "generator": f"terok {__version__}",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "scrubbed": scrub,
    }
    if not scrub:
        document["project"] = project.name
        document["tasks"] = [task_id for task_id, _, _ in harvests]
    document |= {
        "task_count": len(harvests),
        "audit_off_tasks": sum(1 for _, report, _ in harvests if not report.audit_enabled),
        "dns_tiers": sorted({tier.value for tier in dns_tiers if tier is not None}),
        "domain_attribution": attribution,
        "enabled_sets": [name for name in enabled if not scrub or name in EGRESS_SETS],
        "entries": [
            _export_entry(
                c, tiers_of[_normalized(c.target)], len(seen_by[_normalized(c.target)]), scrub=scrub
            )
            for c in candidates
        ],
        "suggestions": [suggestion.as_dict() for suggestion in suggestions],
    }
    if scrub:
        document["scrubbed_out"] = dropped
    return document


def _export_entry(
    candidate: EgressCandidate, tiers: set[str], tasks: int, *, scrub: bool
) -> dict[str, Any]:
    """One merged target as an export entry."""
    entry: dict[str, Any] = {
        "target": candidate.target,
        "tier": candidate.tier,
        "tiers": sorted(tiers, key=lambda tier: (_strictness(tier), tier)),
        "remedies": list(candidate.remedies),
        "actions": list(candidate.actions),
        "count": candidate.count,
        "tasks": tasks,
        "first_seen": candidate.first_seen,
        "last_seen": candidate.last_seen,
        "ports": list(candidate.ports),
    }
    if not scrub:
        entry["addresses"] = list(candidate.addresses)
    return entry


def _scrubbed(
    candidates: Iterable[EgressCandidate],
) -> tuple[list[EgressCandidate], dict[str, int]]:
    """Drop what identifies the network an export came from; count what went, and why."""
    kept: list[EgressCandidate] = []
    dropped = {"addresses": 0, "internal_names": 0}
    for candidate in candidates:
        if candidate.is_address:
            dropped["addresses"] += 1
        elif _is_internal(candidate):
            dropped["internal_names"] += 1
        else:
            kept.append(replace(candidate, addresses=()))
    return kept, dropped


def _is_internal(candidate: EgressCandidate) -> bool:
    """Whether a named target only means something inside the network it was seen on."""
    name = _normalized(candidate.target)
    if "." not in name or name.endswith(_INTERNAL_SUFFIXES):
        return True
    if candidate.tier in (TIER_PRIVATE, TIER_HARD_DENY):
        return True
    addresses = [ip for a in candidate.addresses if (ip := _as_address(a)) is not None]
    return bool(addresses) and not any(ip.is_global for ip in addresses)


# ── Shared machinery ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _TaskContext:
    """A task as promotion needs it: its project, its shield, its container."""

    project: ProjectConfig
    task_id: str
    manager: ShieldManager
    """Built with terok's configuration, so the kill switch applies."""
    mode: str | None
    container: str | None
    """The podman name; ``None`` when the task never started."""

    @classmethod
    def load(cls, project_name: str, task_id: str) -> _TaskContext:
        """Load the project and the task's record; ``SystemExit`` when either is not there."""
        project = load_project(project_name)
        meta, _ = load_task_meta(project.name, task_id)
        mode = meta.get("mode") or None
        return cls(
            project=project,
            task_id=str(task_id),
            manager=task_shield_manager(project.tasks_root / str(task_id)),
            mode=mode,
            container=container_name(project.name, mode, task_id) if mode else None,
        )

    @property
    def label(self) -> str:
        """``project/task``, the way messages name the task."""
        return f"{self.project.name}/{self.task_id}"

    @cached_property
    def is_running(self) -> bool:
        """Whether the task's container runs now — probed once; a probe that fails reads as not."""
        if self.container is None:
            return False
        try:
            return get_task_container_state(self.project.name, self.task_id, self.mode) == "running"
        except Exception:  # noqa: BLE001 - a probe: the grant then waits for the next start
            return False


@dataclass(frozen=True, slots=True)
class _Grants:
    """What a project's configuration grants at its tasks' next start, by where it comes from."""

    allow: tuple[tuple[str, tuple[str, ...]], ...]
    """The t40 sources — the git upstream host, each enabled set, ``shield.allow``."""
    override: tuple[str, ...]
    """The ``shield.override`` hosts still within their expiry — t10, above every deny."""

    @classmethod
    def of(cls, project: ProjectConfig) -> _Grants:
        """Read the project's grants the way its tasks' next launch composes them."""
        allow: list[tuple[str, tuple[str, ...]]] = []
        if project.upstream_url and (host := git_remote_host(project.upstream_url)):
            allow.append(("the git upstream host", (host,)))
        for name in selected_egress_sets(project.shield_sets):
            allow.append((f"the egress set {name!r}", egress_set_hosts(name, project.known_family)))
        if project.shield_allow:
            allow.append(("shield.allow", tuple(project.shield_allow)))
        today = date.today()
        override = tuple(
            o.host for o in project.shield_override if o.expires is None or o.expires >= today
        )
        return cls(tuple(allow), override)

    def granted_by(self, candidate: EgressCandidate, *, suffix: bool) -> str | None:
        """What grants *candidate*'s target past the tier refusing it, or ``None``.

        An allow (t40) reaches only a target nothing but the allowlist
        refuses; an override (t10) sits above the deny tiers too.  Nothing
        reaches the hard-deny floor.
        """
        target = candidate.target
        if candidate.tier == TIER_ALLOWLIST:
            for label, hosts in self.allow:
                if any(_covers(host, target, suffix=suffix) for host in hosts):
                    return label
        if candidate.tier in (TIER_ALLOWLIST, TIER_OPERATOR_DENY, TIER_SECURITY, TIER_PRIVATE):
            if any(_covers(host, target, suffix=False) for host in self.override):
                return "shield.override"
        return None


def _review(
    project: ProjectConfig, task_id: str, report: HarvestReport, tier: DnsTier | None
) -> EgressReview:
    """Merge, classify and annotate one task's harvest against its project's current policy."""
    attribution = tier is None or tier.runs_dnsmasq
    suffix = tier is not None and tier.live
    grants = _Grants.of(project)
    candidates = [
        replace(c, covered_by=grants.granted_by(c, suffix=suffix)) for c in _merge(report)
    ]
    suggestions = _suggest_sets(candidates, project, suffix=suffix) if attribution else ()
    suggested = {target: s.name for s in suggestions for target in s.covers}
    return EgressReview(
        project=project.name,
        task=task_id,
        candidates=tuple(replace(c, suggested_set=suggested.get(c.target)) for c in candidates),
        suggestions=suggestions,
        audit_enabled=report.audit_enabled,
        dns_tier=tier.value if tier is not None else None,
        domain_attribution=attribution,
        suffix_match=suffix,
    )


def _merge(entries: Iterable[HarvestEntry]) -> list[EgressCandidate]:
    """One candidate per target out of harvest lines keyed by action and target, in review order."""
    groups: dict[str, list[HarvestEntry]] = {}
    for entry in entries:
        groups.setdefault(_normalized(entry.target), []).append(entry)
    candidates = [_candidate(group) for group in groups.values()]
    candidates.sort(key=_review_key)
    return candidates


def _candidate(group: Sequence[HarvestEntry]) -> EgressCandidate:
    """Merge the harvest lines about one target: the strictest tier, with what all of them allow."""
    tier = min((entry.tier for entry in group), key=_strictness)
    remedy_sets = [set(entry.remedies) for entry in group if entry.tier == tier]
    first = next(entry for entry in group if entry.tier == tier)
    remedies = tuple(r for r in first.remedies if all(r in allowed for allowed in remedy_sets))
    seen = [entry.first_seen for entry in group if entry.first_seen]
    return EgressCandidate(
        target=group[0].target,
        tier=tier,
        remedies=remedies,
        actions=tuple(dict.fromkeys(entry.action for entry in group)),
        count=sum(entry.count for entry in group),
        first_seen=min(seen, default=""),
        last_seen=max((entry.last_seen for entry in group), default=""),
        ports=tuple(sorted({port for entry in group for port in entry.ports})),
        addresses=tuple(dict.fromkeys(a for entry in group for a in entry.addresses)),
    )


def _strictness(tier: str) -> int:
    """Rank of *tier*, most restrictive lowest; an unknown tier ranks above every known one."""
    return _STRICTNESS.index(tier) if tier in _STRICTNESS else -1


def _review_key(candidate: EgressCandidate) -> tuple[int, bool, int, str]:
    """Review order: tier (unknown last), remedy-less last, loudest first, then by name."""
    rank = (
        _REVIEW_ORDER.index(candidate.tier)
        if candidate.tier in _REVIEW_ORDER
        else len(_REVIEW_ORDER)
    )
    return (rank, not candidate.destinations, -candidate.count, _normalized(candidate.target))


def _suggest_sets(
    candidates: Sequence[EgressCandidate],
    project: ProjectConfig,
    *,
    suffix: bool,
    user_sets: bool = True,
) -> tuple[SetSuggestion, ...]:
    """A greedy cover of the targets nothing allows yet by the sets the project does not enable.

    Each round takes the set covering the most targets still uncovered —
    ties to the smaller set, then built-in before the operator's own, then
    by name — until no set covers anything left.  A target the project
    grants already is no gap to fill, and a user set that does not load is
    no choice.  *user_sets* ``False`` keeps the operator's own sets out.
    """
    wanted = [c.target for c in candidates if REMEDY_SET in c.remedies and c.covered_by is None]
    if not wanted:
        return ()
    pool = _set_pool(project, user_sets=user_sets)
    suggestions: list[SetSuggestion] = []
    remaining = list(wanted)
    while remaining and (best := _best_cover(pool, remaining, suffix=suffix)) is not None:
        choice, covered = best
        suggestions.append(
            SetSuggestion(choice.name, choice.origin, covered, len(wanted), len(choice.hosts))
        )
        covered_set = set(covered)
        remaining = [target for target in remaining if target not in covered_set]
        pool.remove(choice)
    return tuple(suggestions)


@dataclass(frozen=True, slots=True)
class _SetChoice:
    """An egress set a suggestion may name, with the hosts it would grant this project."""

    name: str
    origin: str
    hosts: tuple[str, ...]


def _set_pool(project: ProjectConfig, *, user_sets: bool) -> list[_SetChoice]:
    """The sets a suggestion may name: not enabled yet, loading, with hosts to grant."""
    enabled = set(selected_egress_sets(project.shield_sets))
    pool: list[_SetChoice] = []
    for egress_set in egress_set_catalog():
        if egress_set.name in enabled or egress_set.error is not None:
            continue
        if egress_set.origin != BUILT_IN_ORIGIN and not user_sets:
            continue
        hosts = (
            resolve_egress_sets((egress_set.name,), project.known_family)
            if egress_set.dynamic
            else egress_set.hosts
        )
        if hosts:
            pool.append(_SetChoice(egress_set.name, egress_set.origin, hosts))
    return pool


def _best_cover(
    pool: Sequence[_SetChoice], remaining: Sequence[str], *, suffix: bool
) -> tuple[_SetChoice, tuple[str, ...]] | None:
    """The set covering the most of *remaining*, with what it covers; ``None`` when none covers any.

    Ties go to the smaller set — it grants less beside what is needed — then
    to a built-in set over the operator's own, then by name.
    """
    best: tuple[tuple[int, int, bool, str], _SetChoice, tuple[str, ...]] | None = None
    for choice in pool:
        covered = tuple(
            target
            for target in remaining
            if any(_covers(host, target, suffix=suffix) for host in choice.hosts)
        )
        if not covered:
            continue
        rank = (-len(covered), len(choice.hosts), choice.origin != BUILT_IN_ORIGIN, choice.name)
        if best is None or rank < best[0]:
            best = (rank, choice, covered)
    return None if best is None else (best[1], best[2])


def _is_built_in(name: str) -> bool:
    """Whether *name* is a built-in set, or ``recommended``, which names every one of them."""
    return name in EGRESS_SETS or name == RECOMMENDED_SET


def _set_covers(project: ProjectConfig, name: str, target: str, *, suffix: bool) -> bool:
    """Whether set *name* grants *target* to this project's tasks."""
    hosts = resolve_egress_sets((name,), project.known_family)
    return any(_covers(host, target, suffix=suffix) for host in hosts)


def _covers(entry: str, target: str, *, suffix: bool) -> bool:
    """Whether a policy *entry* grants *target*.

    By exact name, by address or range for an address target, and — with
    *suffix*, the ``dnsmasq-live`` tier's behaviour — a listed domain also
    covers its subdomains.
    """
    entry, target = _normalized(entry), _normalized(target)
    if entry == target:
        return True
    target_net, entry_net = _as_network(target), _as_network(entry)
    if target_net is not None:
        return (
            entry_net is not None
            and target_net.version == entry_net.version
            and target_net.subnet_of(entry_net)  # type: ignore[arg-type]  # same version, checked
        )
    return entry_net is None and suffix and target.endswith(f".{entry}")


def _normalized(host: str) -> str:
    """*host* compared the way DNS compares names: case-folded, without a trailing dot."""
    return host.strip().lower().rstrip(".")


def _as_network(text: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """*text* as an address range (an address is a one-address range), or ``None`` for a name."""
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None


def _as_address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """*text* as one address, or ``None`` when it is not one."""
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_VERSION",
    "PROMOTION_DESTINATIONS",
    "EgressCandidate",
    "EgressReview",
    "PromotionRefused",
    "PromotionResult",
    "PromotionSeverity",
    "SetSuggestion",
    "export_egress",
    "promote_egress",
    "review_egress",
    "tier_heading",
]
