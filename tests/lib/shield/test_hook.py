# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for the OCI hook (fail-closed behavior, OCI state parsing)."""

import json
import unittest
import unittest.mock
from io import StringIO
from pathlib import Path


class TestHookMain(unittest.TestCase):
    """Tests for hook_main fail-closed behavior."""

    @unittest.mock.patch("sys.stdin", new_callable=StringIO)
    def test_invalid_json_exits(self, mock_stdin: StringIO) -> None:
        """Invalid OCI state JSON causes fatal exit."""
        mock_stdin.write("not json")
        mock_stdin.seek(0)

        from terok.lib.security.shield.hook import hook_main

        with self.assertRaises(SystemExit) as ctx:
            hook_main()
        self.assertIn("cannot parse", str(ctx.exception))

    @unittest.mock.patch("sys.stdin", new_callable=StringIO)
    def test_missing_annotation_exits(self, mock_stdin: StringIO) -> None:
        """Missing shield annotation causes fatal exit."""
        mock_stdin.write(json.dumps({"id": "abc123", "annotations": {}}))
        mock_stdin.seek(0)

        from terok.lib.security.shield.hook import hook_main

        with self.assertRaises(SystemExit) as ctx:
            hook_main()
        self.assertIn("annotation is empty", str(ctx.exception))

    @unittest.mock.patch("terok.lib.security.shield.hook.nft")
    @unittest.mock.patch("terok.lib.security.shield.hook.profile_path")
    @unittest.mock.patch("sys.stdin", new_callable=StringIO)
    def test_profile_not_found_exits(
        self,
        mock_stdin: StringIO,
        mock_profile: unittest.mock.Mock,
        mock_nft: unittest.mock.Mock,
    ) -> None:
        """Missing profile causes fatal exit."""
        mock_stdin.write(
            json.dumps(
                {
                    "id": "abc123def456",
                    "annotations": {"terok.shield.profiles": "dev-standard"},
                }
            )
        )
        mock_stdin.seek(0)
        mock_nft.return_value = ""
        mock_profile.side_effect = FileNotFoundError("not found")

        from terok.lib.security.shield.hook import hook_main

        with self.assertRaises(SystemExit) as ctx:
            hook_main()
        self.assertIn("not found", str(ctx.exception))


class TestGenerateEntrypoint(unittest.TestCase):
    """Tests for generate_entrypoint."""

    def test_is_python_script(self) -> None:
        from terok.lib.security.shield.hook import generate_entrypoint

        ep = generate_entrypoint()
        self.assertTrue(ep.startswith("#!/usr/bin/env python3"))

    def test_imports_hook_main(self) -> None:
        from terok.lib.security.shield.hook import generate_entrypoint

        ep = generate_entrypoint()
        self.assertIn("hook_main", ep)


class TestGenerateHookJson(unittest.TestCase):
    """Tests for generate_hook_json."""

    def test_valid_json(self) -> None:
        from terok.lib.security.shield.hook import generate_hook_json

        result = generate_hook_json(Path("/usr/local/bin/hook"))
        parsed = json.loads(result)
        self.assertEqual(parsed["version"], "1.0.0")
        self.assertIn("createContainer", parsed["stages"])

    def test_annotation_pattern(self) -> None:
        from terok.lib.security.shield.hook import generate_hook_json

        result = generate_hook_json(Path("/usr/local/bin/hook"))
        parsed = json.loads(result)
        annotations = parsed["when"]["annotations"]
        self.assertTrue(any("terok" in k for k in annotations))
