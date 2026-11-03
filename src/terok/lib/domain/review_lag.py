# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Review-lag detection: gate branches ahead of an open MR/PR on the forge.

The gatekeeping failure this catches: the operator reviews agent work in
the gate, opens an MR — and then forgets to push follow-up gate commits,
so CodeRabbit/CI keep reviewing stale code.  The predicate needs no new
state and no mode switch:

    an open MR/PR whose source branch is ``B`` exists on the forge,
    and the gate's ``B`` tip is ahead of the SHA the forge knows.

The open MR *is* the "external review started" signal — before it
exists, gate-ahead is the normal local-review posture and stays silent.
Enumeration intersects two ground truths: the gate's branch heads (the
complete record of what agents published — tasks create and switch
branches freely, so task metadata is never consulted) and the forge's
open MRs, fetched with one API call through the operator's own ``gh`` /
``glab`` login (read-only, host-side; containers never see the forge).

Warnings are lines of text (``!42 border-wave-1 +3``), by design: the
operator resolves the lag from their own checkout, so terok only needs
to make the state impossible to miss, not actionable.
"""

from __future__ import annotations

import json
import logging
import subprocess  # nosec B404 - forge queries go through the operator's own gh/glab CLIs
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

from terok_util import require_host_tool, write_untrusted_text

from terok.lib.integrations.executor import agent_input_dir
from terok.lib.orchestration.tasks import agent_config_dir, iter_task_ids, tasks_meta_dir
from terok.lib.util.net import git_remote_host

if TYPE_CHECKING:
    from terok.lib.core.project_model import ProjectConfig
    from terok.lib.integrations.sandbox import GitGate

#: Forge hostnames that speak the GitHub API; everything else is assumed
#: to be a GitLab instance (self-hosted GitLabs are common, self-hosted
#: GitHubs are not part of the terok workflow).
_GITHUB_HOST = "github.com"

#: Ceiling on open reviews fetched per project — one page of the largest
#: size both forges serve.  More than this many simultaneously open MRs
#: means review lag is not the project's most pressing problem.
_MAX_OPEN_REVIEWS = 100

_FORGE_QUERY_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class OpenReview:
    """One open MR/PR as the forge reports it."""

    number: int
    source_branch: str
    sha: str
    #: Display prefix — ``!`` for GitLab MRs, ``#`` for GitHub PRs.
    prefix: str


@dataclass(frozen=True)
class ReviewLagEntry:
    """One gate branch that external review is no longer seeing."""

    branch: str
    review: OpenReview
    commits_ahead: int

    def __str__(self) -> str:
        """Render the warning line (``!42 border-wave-1 +3``)."""
        return f"{self.review.prefix}{self.review.number} {self.branch} +{self.commits_ahead}"


def _forge_command(upstream_url: str) -> list[str] | None:
    """Build the ``gh``/``glab`` invocation listing open reviews.

    Returns ``None`` for URLs that don't name a forge project (local
    paths, bare hosts) — review lag is then simply not computable.
    """
    host, path = _split_forge_url(upstream_url)
    if not host or "/" not in path:
        return None
    if host == _GITHUB_HOST:
        return [
            "gh",
            "api",
            f"repos/{path}/pulls?state=open&per_page={_MAX_OPEN_REVIEWS}",
        ]
    return [
        "glab",
        "api",
        "--hostname",
        host,
        f"projects/{quote(path, safe='')}/merge_requests?state=opened&per_page={_MAX_OPEN_REVIEWS}",
    ]


def _split_forge_url(upstream_url: str) -> tuple[str, str]:
    """Return ``(host, owner/repo)`` from an HTTPS or SSH remote URL."""
    host = git_remote_host(upstream_url)
    if host is None:
        return "", ""
    path = urlsplit(upstream_url).path if "://" in upstream_url else upstream_url.partition(":")[2]
    return host, path.strip("/").removesuffix(".git")


def _parse_reviews(payload: str, *, github: bool) -> list[OpenReview]:
    """Parse the forge's JSON into [`OpenReview`][terok.lib.domain.review_lag.OpenReview] entries."""
    reviews = []
    for item in json.loads(payload):
        if github:
            review = OpenReview(
                number=item["number"],
                source_branch=item["head"]["ref"],
                sha=item["head"]["sha"],
                prefix="#",
            )
        else:
            review = OpenReview(
                number=item["iid"],
                source_branch=item["source_branch"],
                sha=item["sha"],
                prefix="!",
            )
        reviews.append(review)
    return reviews


def fetch_open_reviews(upstream_url: str) -> list[OpenReview] | None:
    """List the forge's open MRs/PRs for *upstream_url*.

    ``None`` means the query could not run (no forge in the URL, CLI
    missing or unauthenticated, network down) — callers keep their
    previous warning state rather than mistaking silence for "all
    pushed".  An empty list is a real answer: nothing under external
    review.
    """
    command = _forge_command(upstream_url)
    if command is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 - argv is a fixed CLI verb + values derived from project config
            command,
            executable=require_host_tool(command[0]),
            capture_output=True,
            text=True,
            timeout=_FORGE_QUERY_TIMEOUT_SECONDS,
            check=True,
        )
        return _parse_reviews(result.stdout, github=command[0] == "gh")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def review_lag_entries(gate: GitGate, reviews: list[OpenReview]) -> list[ReviewLagEntry]:
    """Intersect the gate's branch heads with *reviews*; return the stale ones.

    A branch counts as lagging when the gate knows commits the forge SHA
    doesn't — measured by the gate's own ancestry comparison, so a gate
    merely *behind* the forge (operator pushed fixups directly) never
    warns here.
    """
    heads = gate.branch_heads()
    entries = []
    for review in reviews:
        gate_sha = heads.get(review.source_branch)
        if gate_sha is None or gate_sha == review.sha:
            continue
        staleness = gate.compare_vs_upstream(review.source_branch)
        if staleness.error is None and (staleness.commits_ahead or 0) > 0:
            entries.append(
                ReviewLagEntry(
                    branch=review.source_branch,
                    review=review,
                    commits_ahead=staleness.commits_ahead or 0,
                )
            )
    return sorted(entries, key=lambda e: e.branch)


def format_review_status(entries: list[ReviewLagEntry]) -> str:
    """Render the ``review-status`` file body — one warning per line, or empty."""
    return "".join(f"{entry}\n" for entry in entries)


#: Host-written input (``~/.terok/input/review-status`` in the container),
#: read by the tmux status line and the agent.
REVIEW_STATUS_FILENAME = "review-status"


def write_review_status_files(agent_config_dirs: Iterable[Path], content: str) -> None:
    """Write *content* into every task's read-only review-status input.

    Empty content is still written — the warning is level-triggered, so
    clearing it (operator pushed, MR merged) matters as much as raising
    it. Writes are atomic; failures are logged without interrupting the other
    tasks' updates.
    """
    for config_dir in agent_config_dirs:
        try:
            write_untrusted_text(
                agent_input_dir(config_dir.parent) / REVIEW_STATUS_FILENAME, content
            )
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Cannot publish review status for %s: %s", config_dir, exc
            )


def refresh_review_lag(project: ProjectConfig) -> list[ReviewLagEntry] | None:
    """Recompute review lag for *project* and surface it into its tasks.

    The one entry point the TUI polls: queries the forge, intersects with
    the gate's heads, and (config permitting) rewrites every task's
    review-status file.  Returns the entries, an empty list for "nothing
    lagging", or ``None`` when the forge could not be queried — callers
    keep their previous state on ``None``.
    """
    if (
        not project.review_lag_enabled
        or project.security_class != "gatekeeping"
        or not project.upstream_url
    ):
        return []
    from terok.lib.domain.project import make_git_gate

    reviews = fetch_open_reviews(project.upstream_url)
    if reviews is None:
        return None
    entries = review_lag_entries(make_git_gate(project), reviews)
    if project.review_lag_surface_in_tasks:
        meta_dir = tasks_meta_dir(project.name)
        dirs = [agent_config_dir(project.name, tid) for tid in iter_task_ids(meta_dir)]
        write_review_status_files(dirs, format_review_status(entries))
    return entries
