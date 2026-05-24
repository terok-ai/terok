# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the post-W5.A executor shim layer.

``terok.lib.integrations.executor`` exposes thin function shims
(``authenticate``, ``build_base_images``, ``build_sidecar_image``,
``ensure_default_l1``, ``image_agents``, ``set_global_image_agents``,
…) that wrap the new ``ImageBuilder`` / ``Authenticator`` /
``ExecutorConfigView`` class API in terok-executor.  These tests pin
the call shape so a future API change in terok-executor doesn't
silently drift the terok-facing surface.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from terok.lib.integrations import executor as integ_executor


def test_authenticate_routes_through_authenticator() -> None:
    """``authenticate(provider, …)`` constructs an ``Authenticator`` and runs it."""
    with mock.patch("terok.lib.integrations.executor.Authenticator") as cls:
        integ_executor.authenticate(
            "proj-1",
            "claude",
            mounts_dir=Path("/m"),
            image="some:tag",
            expose_token=True,
            oauth_enabled=False,
        )
    cls.assert_called_once_with("claude")
    cls.return_value.run.assert_called_once_with(
        "proj-1",
        mounts_dir=Path("/m"),
        image="some:tag",
        expose_token=True,
        oauth_enabled=False,
    )


def test_build_base_images_routes_through_image_builder() -> None:
    """``build_base_images`` calls ``ImageBuilder(base, family).build_base``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        integ_executor.build_base_images(
            "fedora:44",
            family="rpm",
            agents=("claude",),
            rebuild=True,
            full_rebuild=False,
            tag_as_default=True,
        )
    cls.assert_called_once_with("fedora:44", family="rpm")
    cls.return_value.build_base.assert_called_once()


def test_build_sidecar_image_routes_through_image_builder() -> None:
    """``build_sidecar_image`` calls ``ImageBuilder.build_sidecar``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        integ_executor.build_sidecar_image("ubuntu:24.04", family="deb", tool_name="custom")
    cls.assert_called_once_with("ubuntu:24.04", family="deb")
    cls.return_value.build_sidecar.assert_called_once()


def test_ensure_default_l1_routes_through_image_builder() -> None:
    """``ensure_default_l1`` calls ``ImageBuilder.ensure_default_l1``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        integ_executor.ensure_default_l1("fedora:44", family="rpm", agents=("claude",))
    cls.assert_called_once_with("fedora:44", family="rpm")
    cls.return_value.ensure_default_l1.assert_called_once_with(agents=("claude",))


def test_image_agents_routes_to_static_method() -> None:
    """``image_agents`` delegates to ``ImageBuilder.image_agents``."""
    with mock.patch(
        "terok.lib.integrations.executor.ImageBuilder.image_agents", return_value={"claude"}
    ) as fn:
        assert integ_executor.image_agents("some:tag") == {"claude"}
    fn.assert_called_once_with("some:tag")


def test_set_global_image_agents_routes_to_config_view() -> None:
    """``set_global_image_agents`` delegates to ``ExecutorConfigView.set_image_agents``."""
    with mock.patch(
        "terok.lib.integrations.executor.ExecutorConfigView.set_image_agents",
        return_value=Path("/tmp/cfg.yml"),
    ) as fn:
        assert integ_executor.set_global_image_agents("claude") == Path("/tmp/cfg.yml")
    fn.assert_called_once_with("claude")


def test_detect_family_routes_to_static_method() -> None:
    """``detect_family`` re-exports ``ImageBuilder.detect_family``."""
    with mock.patch(
        "terok.lib.integrations.executor.ImageBuilder.detect_family", return_value="rpm"
    ) as fn:
        assert integ_executor.detect_family("fedora:44") == "rpm"
    fn.assert_called_once_with("fedora:44", None)


def test_l0_image_tag_routes_through_image_builder() -> None:
    """``l0_image_tag`` reads ``ImageBuilder(base).l0_tag``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        cls.return_value.l0_tag = "terok-l0:fedora-44"
        assert integ_executor.l0_image_tag("fedora:44") == "terok-l0:fedora-44"


def test_l1_image_tag_routes_through_image_builder() -> None:
    """``l1_image_tag(base, agents)`` calls ``ImageBuilder(base).l1_tag(agents)``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        cls.return_value.l1_tag.return_value = "terok-l1-cli:fedora-44-claude"
        result = integ_executor.l1_image_tag("fedora:44", ("claude",))
    assert result == "terok-l1-cli:fedora-44-claude"
    cls.return_value.l1_tag.assert_called_once_with(("claude",))


def test_render_l0_routes_through_image_builder() -> None:
    """``render_l0`` calls ``ImageBuilder(base, family).render_l0``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        cls.return_value.render_l0.return_value = "Dockerfile"
        assert integ_executor.render_l0("fedora:44", family="rpm") == "Dockerfile"
    cls.assert_called_once_with("fedora:44", family="rpm")


def test_render_l1_routes_through_image_builder() -> None:
    """``render_l1`` calls ``ImageBuilder.render_l1`` with l0, agents, cache_bust."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder") as cls:
        cls.return_value.render_l1.return_value = "L1 Dockerfile"
        result = integ_executor.render_l1(
            "l0:tag", family="rpm", agents=("claude",), cache_bust="9"
        )
    assert result == "L1 Dockerfile"
    cls.return_value.render_l1.assert_called_once_with("l0:tag", agents=("claude",), cache_bust="9")


def test_stage_scripts_routes_to_static_method(tmp_path: Path) -> None:
    """``stage_scripts`` delegates to ``ImageBuilder.stage_scripts``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder.stage_scripts") as fn:
        integ_executor.stage_scripts(tmp_path)
    fn.assert_called_once_with(tmp_path)


def test_stage_toad_agents_routes_to_static_method(tmp_path: Path) -> None:
    """``stage_toad_agents`` delegates to ``ImageBuilder.stage_toad_agents``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder.stage_toad_agents") as fn:
        integ_executor.stage_toad_agents(tmp_path)
    fn.assert_called_once_with(tmp_path)


def test_stage_tmux_config_routes_to_static_method(tmp_path: Path) -> None:
    """``stage_tmux_config`` delegates to ``ImageBuilder.stage_tmux_config``."""
    with mock.patch("terok.lib.integrations.executor.ImageBuilder.stage_tmux_config") as fn:
        integ_executor.stage_tmux_config(tmp_path)
    fn.assert_called_once_with(tmp_path)


def test_get_global_image_agents_routes_to_config_view() -> None:
    """``get_global_image_agents`` delegates to ``ExecutorConfigView.image_agents``."""
    with mock.patch(
        "terok.lib.integrations.executor.ExecutorConfigView.image_agents",
        return_value="claude,codex",
    ) as fn:
        assert integ_executor.get_global_image_agents() == "claude,codex"
    fn.assert_called_once()


def test_get_global_image_base_image_routes_to_config_view() -> None:
    """``get_global_image_base_image`` delegates to ``ExecutorConfigView.image_base_image``."""
    with mock.patch(
        "terok.lib.integrations.executor.ExecutorConfigView.image_base_image",
        return_value="ubuntu:24.04",
    ) as fn:
        assert integ_executor.get_global_image_base_image() == "ubuntu:24.04"
    fn.assert_called_once()
