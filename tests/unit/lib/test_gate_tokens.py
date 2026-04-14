# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate_tokens module."""

from __future__ import annotations

import json
import tempfile
import unittest.mock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from terok_sandbox import (
    SandboxConfig,
    create_token,
    revoke_token_for_task,
)
from terok_sandbox.gate.tokens import TokenStore

from tests.testfs import FAKE_TEROK_STATE_DIR, MISSING_TOKENS_PATH, NONEXISTENT_TOKENS_PATH
from tests.testnet import GATE_PORT


def _test_config(state_dir: Path) -> SandboxConfig:
    """Build a ``SandboxConfig`` with deterministic test paths and ports."""
    return SandboxConfig(
        state_dir=state_dir,
        runtime_dir=state_dir,
        gate_port=GATE_PORT,
        proxy_port=GATE_PORT + 1,
        ssh_agent_port=GATE_PORT + 2,
    )


@contextmanager
def patched_token_file(state_dir: Path | None = None) -> Iterator[Path]:
    """Patch ``SandboxConfig`` so ``TokenStore`` writes to a controlled token file.

    When *state_dir* is ``None``, a fresh temporary directory is used.
    The yielded path is the derived ``token_file_path`` (``state_dir/gate/tokens.json``).
    """
    if state_dir is not None:
        cfg = _test_config(state_dir=state_dir)
        with unittest.mock.patch(
            "terok_sandbox.gate.tokens.SandboxConfig",
            return_value=cfg,
        ):
            yield cfg.token_file_path
        return

    with tempfile.TemporaryDirectory() as td:
        cfg = _test_config(state_dir=Path(td))
        token_path = cfg.token_file_path
        with unittest.mock.patch(
            "terok_sandbox.gate.tokens.SandboxConfig",
            return_value=cfg,
        ):
            yield token_path


def read_token_json(path: Path) -> dict[str, dict[str, str]]:
    """Load the persisted token data from disk."""
    return json.loads(path.read_text())


def _make_store(path: Path) -> TokenStore:
    """Create a pre-wired TokenStore bypassing SandboxConfig resolution."""
    store = TokenStore.__new__(TokenStore)
    store._path = path
    return store


class TestTokenFilePath:
    """Tests for token_file_path."""

    def test_returns_path_under_state_root(self) -> None:
        cfg = SandboxConfig(state_dir=FAKE_TEROK_STATE_DIR)
        path = TokenStore(cfg=cfg).file_path
        assert path == FAKE_TEROK_STATE_DIR / "gate" / "tokens.json"


class TestCreateToken:
    """Tests for create_token."""

    def test_returns_prefixed_token(self) -> None:
        with patched_token_file() as token_path:
            token = create_token("proj-a", "1")
            assert token_path.exists()
        assert token.startswith("terok-g-")
        assert len(token) == 40
        int(token.removeprefix("terok-g-"), 16)

    def test_persists_to_file(self) -> None:
        with patched_token_file() as token_path:
            token = create_token("proj-a", "1")
            data = read_token_json(token_path)
        assert data[token] == {"scope": "proj-a", "task": "1"}

    def test_multiple_tokens_coexist(self) -> None:
        with patched_token_file() as token_path:
            first = create_token("proj-a", "1")
            second = create_token("proj-b", "2")
            data = read_token_json(token_path)
        assert first != second
        assert first in data
        assert second in data


class TestRevokeToken:
    """Tests for revoke_token_for_task."""

    def test_revoke_removes_entry(self) -> None:
        with patched_token_file() as token_path:
            token = create_token("proj-a", "1")
            revoke_token_for_task("proj-a", "1")
            data = read_token_json(token_path)
        assert token not in data

    def test_revoke_nonexistent_is_noop(self) -> None:
        with patched_token_file() as token_path:
            create_token("proj-a", "1")
            revoke_token_for_task("proj-a", "99")
            data = read_token_json(token_path)
        assert len(data) == 1

    def test_revoke_on_missing_file_is_noop(self) -> None:
        with patched_token_file(state_dir=MISSING_TOKENS_PATH.parent):
            revoke_token_for_task("proj-a", "1")


class TestAtomicWrite:
    """Tests for atomic write via TokenStore._write."""

    def test_write_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "sub" / "dir" / "tokens.json"
            _make_store(token_path)._write({"abc": {"scope": "p", "task": "1"}})
            assert read_token_json(token_path) == {"abc": {"scope": "p", "task": "1"}}

    @pytest.mark.parametrize(
        ("path", "content", "expected"),
        [
            (Path(NONEXISTENT_TOKENS_PATH.name), None, {}),
            (Path("tokens.json"), "not json{{{", {}),
            (Path("tokens.json"), json.dumps(["not", "a", "dict"]), {}),
            (
                Path("tokens.json"),
                json.dumps(
                    {
                        "good": {"scope": "p", "task": "1"},
                        "bad_info": "not a dict",
                        "missing_task": {"scope": "p"},
                        "int_scope": {"scope": 123, "task": "1"},
                    }
                ),
                {"good": {"scope": "p", "task": "1"}},
            ),
        ],
        ids=["missing", "corrupt-json", "non-dict-json", "malformed-entries"],
    )
    def test_read_tokens_handles_invalid_inputs(
        self,
        path: Path,
        content: str | None,
        expected: dict[str, dict[str, str]],
    ) -> None:
        """Invalid token files are treated as empty or sanitized."""
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / path
            if content is not None:
                token_path.write_text(content)
            result = _make_store(token_path)._read()
        assert result == expected

    def test_overwrite_and_tmp_cleanup(self) -> None:
        """Verify that successive writes overwrite and leave no temp files."""
        with tempfile.TemporaryDirectory() as td:
            token_path = Path(td) / "tokens.json"
            store = _make_store(token_path)
            store._write({"t1": {"scope": "p", "task": "1"}})
            store._write({"t2": {"scope": "p", "task": "2"}})
            data = read_token_json(token_path)
            assert data == {"t2": {"scope": "p", "task": "2"}}
            assert list(Path(td).glob("*.tmp")) == []
