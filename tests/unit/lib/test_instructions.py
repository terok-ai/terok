# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The global instructions file and the inherit setting of an instructions layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from terok_executor import resolve_instructions

from terok.lib.core.instructions import (
    INHERIT,
    global_instructions_path,
    inherits_instructions,
    set_instructions_inherit,
    with_global_instructions,
)
from terok.lib.orchestration.agent_config import resolve_agent_config

BUNDLED_MARKER = "# "
"""Every rendering of the bundled default holds a Markdown heading; no test text does."""


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point terok at a private user config file, and return its directory."""
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "config.yml"))
    return tmp_path


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Return an empty project directory, apart from the config directory."""
    root = tmp_path / "project"
    root.mkdir()
    return root


def _write_global(config_dir: Path, *, text: str = "", yaml: str = "") -> None:
    if text:
        (config_dir / "instructions.md").write_text(text, encoding="utf-8")
    if yaml:
        (config_dir / "config.yml").write_text(yaml, encoding="utf-8")


def _task_text(project_root: Path, agent_config: dict[str, Any] | None = None) -> str:
    """Resolve the instructions a task of a project would receive."""
    effective = resolve_agent_config("demo", agent_config=agent_config, project_root=project_root)
    return resolve_instructions(effective, "claude", project_root=project_root)


class TestGlobalFile:
    def test_sits_next_to_the_user_config_file(self, config_dir: Path) -> None:
        assert global_instructions_path() == config_dir / "instructions.md"

    def test_config_without_a_file_comes_back_as_it_is(self, config_dir: Path) -> None:
        agent_config = {"model": "opus"}
        assert with_global_instructions(agent_config) is agent_config

    def test_blank_file_counts_as_no_file(self, config_dir: Path) -> None:
        _write_global(config_dir, text="  \n\n")
        assert with_global_instructions({}) == {}

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, [INHERIT, "G"]),
            (INHERIT, [INHERIT, "G"]),
            ([], ["G"]),
            (["a"], ["a", "G"]),
            ([INHERIT, "a"], [INHERIT, "a", "G"]),
            ("flat", ["flat", "G"]),
        ],
        ids=["absent", "bare-inherit", "empty-list", "replacing", "inheriting", "flat-text"],
    )
    def test_joins_every_form_of_the_key(
        self, config_dir: Path, value: object, expected: list[str]
    ) -> None:
        _write_global(config_dir, text="G\n")
        agent_config = {} if value is None else {"instructions": value}
        assert with_global_instructions(agent_config)["instructions"] == expected

    def test_reaches_every_agent_of_a_per_agent_mapping(self, config_dir: Path) -> None:
        """An agent the mapping does not name gets the text through the fallback."""
        _write_global(config_dir, text="G")
        result = with_global_instructions(
            {"instructions": {"claude": "for claude", "codex": None}}
        )["instructions"]
        assert result["claude"] == ["for claude", "G"]
        assert result["codex"] is None  # a null entry still falls back to ``_default``
        assert result["_default"] == [INHERIT, "G"]


class TestWhatATaskReceives:
    """The whole chain, through the resolvers the task runners use."""

    def test_order_is_default_then_global_then_project(
        self, config_dir: Path, project_root: Path
    ) -> None:
        (project_root / "instructions.md").write_text("PROJECT FILE", encoding="utf-8")
        _write_global(config_dir, text="GLOBAL FILE")
        text = _task_text(project_root, {"instructions": [INHERIT, "PROJECT KEY"]})
        positions = [text.index(s) for s in ("GLOBAL FILE", "PROJECT KEY", "PROJECT FILE")]
        assert positions == sorted(positions)
        assert text.index(BUNDLED_MARKER) < positions[0]

    def test_without_project_settings_the_file_follows_the_default(
        self, config_dir: Path, project_root: Path
    ) -> None:
        _write_global(config_dir, text="GLOBAL FILE")
        text = _task_text(project_root)
        assert BUNDLED_MARKER in text
        assert text.rstrip().endswith("GLOBAL FILE")

    @pytest.mark.parametrize("override", [[], ["P"]], ids=["empty", "own-text"])
    def test_project_override_drops_the_file_too(
        self, config_dir: Path, project_root: Path, override: list[str]
    ) -> None:
        _write_global(config_dir, text="GLOBAL FILE")
        text = _task_text(project_root, {"instructions": override})
        assert "GLOBAL FILE" not in text
        assert BUNDLED_MARKER not in text

    def test_global_override_replaces_the_default_and_keeps_the_file(
        self, config_dir: Path, project_root: Path
    ) -> None:
        _write_global(config_dir, text="GLOBAL FILE", yaml="agent:\n  instructions: []\n")
        assert _task_text(project_root).strip() == "GLOBAL FILE"

    def test_key_entries_come_before_the_file(self, config_dir: Path, project_root: Path) -> None:
        yaml = "agent:\n  instructions: [_inherit, GLOBAL KEY]\n"
        _write_global(config_dir, text="GLOBAL FILE", yaml=yaml)
        text = _task_text(project_root)
        assert text.index("GLOBAL KEY") < text.index("GLOBAL FILE")


class TestInheritSetting:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (None, True),
            ("", True),
            ("agent: {}\n", True),
            ("agent:\n  instructions: _inherit\n", True),
            ("agent:\n  instructions: [_inherit, extra]\n", True),
            ("agent:\n  instructions: []\n", False),
            ("agent:\n  instructions: [own]\n", False),
            ("agent:\n  instructions: flat text\n", None),
            ("agent:\n  instructions: {claude: own}\n", None),
            ("agent: [\n", None),
            ("- a\n- list\n", None),
        ],
        ids=[
            "no-file",
            "empty-file",
            "no-key",
            "bare-inherit",
            "inheriting-list",
            "empty-list",
            "replacing-list",
            "flat-text",
            "per-agent",
            "broken-yaml",
            "not-a-mapping",
        ],
    )
    def test_reads_every_form(
        self, tmp_path: Path, content: str | None, expected: bool | None
    ) -> None:
        config = tmp_path / "config.yml"
        if content is not None:
            config.write_text(content, encoding="utf-8")
        assert inherits_instructions(config) is expected

    def test_inherit_with_nothing_set_writes_nothing(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yml"
        set_instructions_inherit(config, True)
        assert not config.exists()

    @pytest.mark.parametrize(
        ("start", "inherit", "expected"),
        [
            ("{}\n", False, []),
            ("agent:\n  instructions: [own]\n", True, [INHERIT, "own"]),
            ("agent:\n  instructions: [_inherit, own]\n", False, ["own"]),
            ("agent:\n  instructions: _inherit\n", False, []),
        ],
        ids=["override-from-nothing", "inherit-keeps-entries", "override-keeps-entries", "bare"],
    )
    def test_switches_and_keeps_the_entries(
        self, tmp_path: Path, start: str, inherit: bool, expected: list[str]
    ) -> None:
        config = tmp_path / "config.yml"
        config.write_text(start, encoding="utf-8")
        set_instructions_inherit(config, inherit)
        assert inherits_instructions(config) is inherit
        from terok.lib.util.yaml import load

        assert load(config.read_text(encoding="utf-8"))["agent"]["instructions"] == expected

    def test_keeps_the_rest_of_the_file(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yml"
        config.write_text("# my note\nagent:\n  model: opus\n", encoding="utf-8")
        set_instructions_inherit(config, False)
        text = config.read_text(encoding="utf-8")
        assert "# my note" in text
        assert "model: opus" in text

    def test_refuses_a_form_it_cannot_switch(self, tmp_path: Path) -> None:
        config = tmp_path / "config.yml"
        original = "agent:\n  instructions: flat text\n"
        config.write_text(original, encoding="utf-8")
        with pytest.raises(ValueError, match="not a list"):
            set_instructions_inherit(config, False)
        assert config.read_text(encoding="utf-8") == original
