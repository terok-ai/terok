# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""AST-based test: nft.py must only import stdlib modules."""

import ast
import unittest
from pathlib import Path


class TestNftImportIsolation(unittest.TestCase):
    """nft.py is the auditable security boundary -- no third-party or terok imports."""

    def test_nft_has_only_stdlib_imports(self) -> None:
        source = (
            Path(__file__).parents[3] / "src" / "terok" / "lib" / "security" / "shield" / "nft.py"
        ).read_text()
        tree = ast.parse(source)
        STDLIB = {"ipaddress", "re", "textwrap", "__future__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self.assertIn(
                        top,
                        STDLIB,
                        f"nft.py imports non-stdlib module: {alias.name}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                self.assertIn(
                    top,
                    STDLIB,
                    f"nft.py imports non-stdlib module: {node.module}",
                )
