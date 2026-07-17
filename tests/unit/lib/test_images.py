# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image-tag helper functions."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from terok.lib.core import images
from terok.lib.core.project_model import ProjectConfig
from tests.testfs import MOCK_BASE

BASE_IMAGE_FUNCS: list[tuple[Callable[[str], str], str]] = [
    (images.base_dev_image, "terok-l0"),
    (images.agent_cli_image, "terok-l1-cli"),
]
BASE_IMAGE_IDS = [prefix for _, prefix in BASE_IMAGE_FUNCS]
BASE_IMAGE_CALLABLES = [func for func, _ in BASE_IMAGE_FUNCS]


@pytest.mark.parametrize(
    ("base_image", "expected"),
    [
        ("", "ubuntu-24.04"),
        ("   ", "ubuntu-24.04"),
        ("ubuntu-22.04", "ubuntu-22.04"),
        ("Ubuntu-22.04", "ubuntu-22.04"),
        ("ubuntu@22#04", "ubuntu-22-04"),
        ("test@#$%^&*()image", "test-image"),
        ("--ubuntu-22.04--", "ubuntu-22.04"),
        ("ubuntu.22.04", "ubuntu.22.04"),
        ("ubuntu_22_04", "ubuntu_22_04"),
        ("ubuntu-22.04_LTS", "ubuntu-22.04_lts"),
        ("@#$%^&*()", "ubuntu-24.04"),
    ],
    ids=[
        "empty",
        "whitespace",
        "simple",
        "lowercases",
        "sanitizes-specials",
        "collapses-specials",
        "strips-edges",
        "preserves-dots",
        "preserves-underscores",
        "mixed-valid-chars",
        "only-specials",
    ],
)
def test_base_tag_simple_cases(base_image: str, expected: str) -> None:
    assert images._base_tag(base_image) == expected


@pytest.mark.parametrize(
    ("name", "prefix_len", "suffix_len"),
    [("a" * 120, 120, 0), ("a" * 121, 111, 8), ("ubuntu@special" * 20, 111, 8)],
    ids=["under-limit", "over-limit", "sanitize-then-truncate"],
)
def test_base_tag_length_and_hash(name: str, prefix_len: int, suffix_len: int) -> None:
    result = images._base_tag(name)
    if len(name) <= 120 and "@" not in name:
        assert result == name
        return
    hash_part = result.split("-")[-1]
    assert len(result) == 120
    assert len(hash_part) == suffix_len
    assert hash_part.isalnum()
    if "@" in name:
        assert "@" not in result
    else:
        assert result.startswith("a" * prefix_len)


def test_base_tag_long_name_hash_is_stable() -> None:
    name = "b" * 150
    first, second = images._base_tag(name), images._base_tag(name)
    assert first == second


def test_base_tag_long_name_hash_changes_for_different_inputs() -> None:
    assert images._base_tag("c" * 150) != images._base_tag("d" * 150)


@pytest.mark.parametrize(
    ("func", "base_image", "expected"),
    [
        (images.base_dev_image, "ubuntu-22.04", "terok-l0:ubuntu-22.04"),
        (images.base_dev_image, "ubuntu@22.04", "terok-l0:ubuntu-22.04"),
        (images.agent_cli_image, "ubuntu-22.04", "terok-l1-cli:ubuntu-22.04"),
        (images.agent_cli_image, "ubuntu@22.04", "terok-l1-cli:ubuntu-22.04"),
    ],
    ids=["l0", "l0-sanitized", "l1-cli", "l1-cli-sanitized"],
)
def test_base_image_functions(
    func: Callable[[str], str],
    base_image: str,
    expected: str,
) -> None:
    assert func(base_image) == expected


@pytest.mark.parametrize(
    ("func", "expected"),
    [
        (images.project_cli_image, "my-project:l2-cli"),
        (images.project_dev_image, "my-project:l2-dev"),
    ],
    ids=["project-cli", "project-dev"],
)
def test_project_image_functions(func: Callable[[str], str], expected: str) -> None:
    assert func("my-project") == expected


@pytest.mark.parametrize(("func", "prefix"), BASE_IMAGE_FUNCS, ids=BASE_IMAGE_IDS)
def test_base_image_functions_handle_empty_input(
    func: Callable[[str], str],
    prefix: str,
) -> None:
    assert func("") == f"{prefix}:ubuntu-24.04"


@pytest.mark.parametrize("func", BASE_IMAGE_CALLABLES, ids=BASE_IMAGE_IDS)
def test_base_image_functions_share_long_tag_generation(func: Callable[[str], str]) -> None:
    assert len(func("x" * 150).split(":", 1)[1]) == 120


def test_base_image_functions_reuse_the_same_long_tag() -> None:
    long_name = "x" * 150
    tags = {func(long_name).split(":", 1)[1] for func, _ in BASE_IMAGE_FUNCS}
    assert len(tags) == 1


# ── installed_agents / installed_agents_for_project ──────────────────


def _patch_labels(mock_runtime, labels: dict[str, str]) -> None:
    """Configure the autouse ``mock_runtime`` so ``Image.labels()`` returns *labels*.

    An empty dict mimics podman's behaviour for both missing images and
    unlabeled images — the distinction doesn't matter for the callers
    under test here.
    """
    mock_runtime.image.return_value.labels.return_value = labels


def _patch_labels_by_tag(mock_runtime, labels_by_tag: dict[str, dict[str, str]]) -> None:
    """Configure ``mock_runtime`` so each image tag answers with its own labels."""

    def _image(tag: str):
        image = MagicMock(name=f"image[{tag}]")
        image.labels.return_value = labels_by_tag.get(tag, {})
        return image

    mock_runtime.image.side_effect = _image


def _project(agents: str = "all") -> ProjectConfig:
    """Minimal project whose L2 tag is ``my-project:l2-cli``."""
    root = MOCK_BASE / "images" / "my-project"
    return ProjectConfig(
        name="my-project",
        security_class="online",
        upstream_url=None,
        default_branch="main",
        root=root,
        tasks_root=root / "tasks",
        gate_path=root / "gate",
        staging_root=None,
        base_image="ubuntu-24.04",
        agents=agents,
    )


def test_installed_agents_parses_label(mock_runtime) -> None:
    _patch_labels(mock_runtime, {"ai.terok.agents": "claude,codex,opencode"})
    assert images.installed_agents("terok-l1-cli:test") == frozenset(
        {"claude", "codex", "opencode"}
    )


def test_installed_agents_missing_label_returns_empty(mock_runtime) -> None:
    _patch_labels(mock_runtime, {})
    assert images.installed_agents("terok-l1-cli:legacy") == frozenset()


def test_installed_agents_missing_image_returns_empty(mock_runtime) -> None:
    # ``runtime.image(...).labels()`` returns {} when the image is absent —
    # same end-state as an unlabeled image, which is correct for the
    # unrestricted-fallback semantics callers rely on.
    _patch_labels(mock_runtime, {})
    assert images.installed_agents("terok-l1-cli:nope") == frozenset()


def test_installed_agents_for_project_reads_the_l2_image_not_the_l1_alias(
    mock_runtime,
) -> None:
    # The unsuffixed L1 tag is a default alias for the user's *global*
    # agent selection, so the answer must come from the L2 image the
    # task actually boots.
    _patch_labels_by_tag(
        mock_runtime,
        {
            "terok-l1-cli:ubuntu-24.04": {"ai.terok.agents": "claude,codex,vibe"},
            "my-project:l2-cli": {"ai.terok.agents": "claude"},
        },
    )
    assert images.installed_agents_for_project(_project()) == frozenset({"claude"})


def test_installed_agents_for_project_falls_back_to_project_selection(mock_runtime) -> None:
    # Unlabeled / absent L2 image: the offer comes from the project
    # definition (what a rebuild would install), never "every known agent".
    _patch_labels(mock_runtime, {})
    assert images.installed_agents_for_project(_project("claude,codex")) == frozenset(
        {"claude", "codex"}
    )


def test_installed_agents_for_project_fallback_resolves_all(mock_runtime) -> None:
    from terok.lib.integrations.executor import AgentRoster

    _patch_labels(mock_runtime, {})
    expected = frozenset(AgentRoster.shared().resolve_selection("all"))
    assert images.installed_agents_for_project(_project("all")) == expected


def test_installed_agents_for_project_unresolvable_selection_is_empty(mock_runtime) -> None:
    _patch_labels(mock_runtime, {})
    assert images.installed_agents_for_project(_project("no-such-agent")) == frozenset()


# ── require_agent_installed ───────────────────────────────────────────


def test_require_agent_installed_accepts_labeled_agent(mock_runtime) -> None:
    _patch_labels_by_tag(mock_runtime, {"my-project:l2-cli": {"ai.terok.agents": "claude,codex"}})
    images.require_agent_installed(_project(), "claude")


def test_require_agent_installed_rejects_agent_missing_from_l2_label(mock_runtime) -> None:
    # ``vibe`` is in the default-alias L1 label but not in this project's
    # L2 image — the guard must agree with the picker and reject.
    _patch_labels_by_tag(
        mock_runtime,
        {
            "terok-l1-cli:ubuntu-24.04": {"ai.terok.agents": "claude,vibe"},
            "my-project:l2-cli": {"ai.terok.agents": "claude"},
        },
    )
    project = _project()
    with pytest.raises(SystemExit, match="not available in the image"):
        images.require_agent_installed(project, "vibe")


def test_require_agent_installed_uses_project_selection_when_unlabeled(mock_runtime) -> None:
    _patch_labels(mock_runtime, {})
    project = _project("claude")
    images.require_agent_installed(project, "claude")
    with pytest.raises(SystemExit, match="not available in the image"):
        images.require_agent_installed(project, "codex")


def test_require_agent_installed_treats_unknown_set_as_unrestricted(mock_runtime) -> None:
    # No label anywhere and an unresolvable project selection: the set is
    # unknown, so the guard stays permissive rather than blocking launches.
    _patch_labels(mock_runtime, {})
    images.require_agent_installed(_project("no-such-agent"), "claude")
