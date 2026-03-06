# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.ui_utils.editor import _resolve_editor, open_in_editor


def _only_custom_editor(cmd: str) -> str | None:
    return cmd if cmd == "/usr/bin/custom-editor" else None


def _only_nano(cmd: str) -> str | None:
    return cmd if cmd == "nano" else None


def _only_vi(cmd: str) -> str | None:
    return cmd if cmd == "vi" else None


class ResolveEditorTests(unittest.TestCase):
    """Tests for _resolve_editor()."""

    @unittest.mock.patch.dict("os.environ", {"EDITOR": "/usr/bin/custom-editor"})
    @unittest.mock.patch("shutil.which", side_effect=_only_custom_editor)
    def test_prefers_editor_env_var(self, _which: unittest.mock.Mock) -> None:
        self.assertEqual(_resolve_editor(), "/usr/bin/custom-editor")

    @unittest.mock.patch.dict("os.environ", {"EDITOR": ""})
    @unittest.mock.patch("shutil.which", side_effect=_only_nano)
    def test_falls_back_to_nano(self, _which: unittest.mock.Mock) -> None:
        self.assertEqual(_resolve_editor(), "nano")

    @unittest.mock.patch.dict("os.environ", {"EDITOR": ""})
    @unittest.mock.patch("shutil.which", side_effect=_only_vi)
    def test_falls_back_to_vi(self, _which: unittest.mock.Mock) -> None:
        self.assertEqual(_resolve_editor(), "vi")

    @unittest.mock.patch.dict("os.environ", {"EDITOR": ""})
    @unittest.mock.patch("shutil.which", return_value=None)
    def test_returns_none_when_no_editor(self, _which: unittest.mock.Mock) -> None:
        self.assertIsNone(_resolve_editor())

    @unittest.mock.patch.dict("os.environ", {"EDITOR": "   "})
    @unittest.mock.patch("shutil.which", side_effect=_only_nano)
    def test_ignores_whitespace_only_editor(self, _which: unittest.mock.Mock) -> None:
        self.assertEqual(_resolve_editor(), "nano")

    @unittest.mock.patch.dict("os.environ", {"EDITOR": "nonexistent"})
    @unittest.mock.patch("shutil.which", side_effect=_only_nano)
    def test_editor_env_not_on_path_falls_back(self, _which: unittest.mock.Mock) -> None:
        self.assertEqual(_resolve_editor(), "nano")


class OpenInEditorTests(unittest.TestCase):
    """Tests for open_in_editor()."""

    @unittest.mock.patch("terok.ui_utils.editor._resolve_editor", return_value="nano")
    @unittest.mock.patch("subprocess.run")
    def test_success_returns_true(
        self,
        mock_run: unittest.mock.Mock,
        _resolve: unittest.mock.Mock,
    ) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yml") as f:
            path = Path(f.name)
            self.assertTrue(open_in_editor(path))
            mock_run.assert_called_once_with(["nano", str(path)], check=True)

    @unittest.mock.patch("terok.ui_utils.editor._resolve_editor", return_value=None)
    def test_no_editor_returns_false(self, _resolve: unittest.mock.Mock) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yml") as f:
            self.assertFalse(open_in_editor(Path(f.name)))

    @unittest.mock.patch("terok.ui_utils.editor._resolve_editor", return_value="nano")
    @unittest.mock.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "nano"),
    )
    def test_editor_failure_returns_false(
        self,
        _run: unittest.mock.Mock,
        _resolve: unittest.mock.Mock,
    ) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yml") as f:
            self.assertFalse(open_in_editor(Path(f.name)))

    @unittest.mock.patch("terok.ui_utils.editor._resolve_editor", return_value="nano")
    @unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError)
    def test_editor_not_found_returns_false(
        self,
        _run: unittest.mock.Mock,
        _resolve: unittest.mock.Mock,
    ) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yml") as f:
            self.assertFalse(open_in_editor(Path(f.name)))

    @unittest.mock.patch("terok.ui_utils.editor._resolve_editor", return_value=None)
    @unittest.mock.patch("builtins.print")
    def test_no_editor_prints_message(
        self,
        mock_print: unittest.mock.Mock,
        _resolve: unittest.mock.Mock,
    ) -> None:
        with tempfile.NamedTemporaryFile(suffix=".yml") as f:
            open_in_editor(Path(f.name))
            mock_print.assert_called_once()
            self.assertIn("EDITOR", mock_print.call_args[0][0])
            self.assertIs(mock_print.call_args[1].get("file"), sys.stderr)


if __name__ == "__main__":
    unittest.main()
