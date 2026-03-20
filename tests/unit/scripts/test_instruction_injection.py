# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ACP wrapper instruction relay.

Validates that ACP wrappers pass terok instructions to agents via native
per-agent mechanisms (CLI flags, env vars, per-task config overlays)
without touching workspace files or shared config directories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "src" / "terok" / "resources" / "scripts"

_INSTR_PATH = "/home/dev/.terok/instructions.md"

# Wrappers that should relay instructions from the per-task instructions.md
_INSTRUCTION_WRAPPERS = [
    "terok-claude-acp",
    "terok-codex-acp",
    "terok-vibe-acp",
    "terok-copilot-acp",
]


class TestClaudeAcpInstructions:
    """Claude ACP wrapper relays instructions via --append-system-prompt."""

    def test_uses_append_system_prompt_flag(self) -> None:
        """Wrapper passes --append-system-prompt when instructions exist."""
        content = (SCRIPTS_DIR / "terok-claude-acp").read_text(encoding="utf-8")
        assert "--append-system-prompt" in content

    def test_reads_instructions_file(self) -> None:
        """Wrapper references the per-task instructions.md path."""
        content = (SCRIPTS_DIR / "terok-claude-acp").read_text(encoding="utf-8")
        assert _INSTR_PATH in content

    def test_conditional_on_file_existence(self) -> None:
        """Flag is only passed when the instructions file exists."""
        content = (SCRIPTS_DIR / "terok-claude-acp").read_text(encoding="utf-8")
        assert f"-f {_INSTR_PATH}" in content


class TestCodexAcpInstructions:
    """Codex ACP wrapper relays instructions via -c model_instructions_file."""

    def test_uses_model_instructions_file_flag(self) -> None:
        """Wrapper passes -c model_instructions_file when instructions exist."""
        content = (SCRIPTS_DIR / "terok-codex-acp").read_text(encoding="utf-8")
        assert "model_instructions_file" in content

    def test_reads_instructions_file(self) -> None:
        """Wrapper references the per-task instructions.md path."""
        content = (SCRIPTS_DIR / "terok-codex-acp").read_text(encoding="utf-8")
        assert _INSTR_PATH in content

    def test_conditional_on_file_existence(self) -> None:
        """Flag is only passed when the instructions file exists."""
        content = (SCRIPTS_DIR / "terok-codex-acp").read_text(encoding="utf-8")
        assert f"-f {_INSTR_PATH}" in content


class TestVibeAcpInstructions:
    """Vibe ACP wrapper relays instructions via per-task VIBE_HOME overlay."""

    def test_creates_vibe_home_overlay(self) -> None:
        """Wrapper builds a per-task VIBE_HOME directory."""
        content = (SCRIPTS_DIR / "terok-vibe-acp").read_text(encoding="utf-8")
        assert "/home/dev/.terok/vibe-home" in content

    def test_exports_vibe_home(self) -> None:
        """Wrapper exports VIBE_HOME pointing at the per-task overlay."""
        content = (SCRIPTS_DIR / "terok-vibe-acp").read_text(encoding="utf-8")
        assert "export VIBE_HOME=" in content

    def test_copies_instructions_as_agents_md(self) -> None:
        """Instructions are placed as AGENTS.md in the overlay."""
        content = (SCRIPTS_DIR / "terok-vibe-acp").read_text(encoding="utf-8")
        assert "AGENTS.md" in content
        assert _INSTR_PATH in content

    def test_symlinks_real_vibe_config(self) -> None:
        """Overlay symlinks entries from the real ~/.vibe to preserve config."""
        content = (SCRIPTS_DIR / "terok-vibe-acp").read_text(encoding="utf-8")
        assert "ln -sf" in content
        assert ".vibe" in content

    def test_conditional_on_file_existence(self) -> None:
        """Overlay is only created when the instructions file exists."""
        content = (SCRIPTS_DIR / "terok-vibe-acp").read_text(encoding="utf-8")
        # Wrapper defines a variable for the path and tests it with -f
        assert _INSTR_PATH in content
        assert '-f "$_TEROK_INSTR"' in content


class TestCopilotAcpInstructions:
    """Copilot ACP wrapper relays instructions via COPILOT_CUSTOM_INSTRUCTIONS_DIRS."""

    def test_exports_custom_instructions_dirs(self) -> None:
        """Wrapper sets COPILOT_CUSTOM_INSTRUCTIONS_DIRS to per-task subdir."""
        content = (SCRIPTS_DIR / "terok-copilot-acp").read_text(encoding="utf-8")
        assert "COPILOT_CUSTOM_INSTRUCTIONS_DIRS" in content

    def test_creates_correctly_named_file(self) -> None:
        """Instructions are copied with *.instructions.md suffix for Copilot."""
        content = (SCRIPTS_DIR / "terok-copilot-acp").read_text(encoding="utf-8")
        assert ".instructions.md" in content

    def test_reads_instructions_file(self) -> None:
        """Wrapper references the per-task instructions.md path."""
        content = (SCRIPTS_DIR / "terok-copilot-acp").read_text(encoding="utf-8")
        assert _INSTR_PATH in content

    def test_conditional_on_file_existence(self) -> None:
        """Env var is only set when the instructions file exists."""
        content = (SCRIPTS_DIR / "terok-copilot-acp").read_text(encoding="utf-8")
        assert _INSTR_PATH in content
        assert '-f "$_TEROK_INSTR"' in content


class TestNoWorkspaceModification:
    """No ACP wrapper or init script modifies workspace convention files."""

    @pytest.mark.parametrize("script", _INSTRUCTION_WRAPPERS)
    def test_wrapper_does_not_touch_workspace(self, script: str) -> None:
        """ACP wrappers must not write to /workspace."""
        content = (SCRIPTS_DIR / script).read_text(encoding="utf-8")
        assert "/workspace/" not in content

    def test_init_script_does_not_inject_convention_files(self) -> None:
        """init-ssh-and-repo.sh must not write CLAUDE.md or AGENTS.md."""
        content = (SCRIPTS_DIR / "init-ssh-and-repo.sh").read_text(encoding="utf-8")
        assert "CLAUDE.md" not in content
        assert "AGENTS.md" not in content
