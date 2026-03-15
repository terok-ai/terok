# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""MkDocs gen-files hook for the integration test map."""

from __future__ import annotations

import sys
from pathlib import Path

import mkdocs_gen_files

DOCS_DIR = Path(__file__).resolve().parent
if str(DOCS_DIR) not in sys.path:
    sys.path.insert(0, str(DOCS_DIR))

import test_map  # noqa: E402

with mkdocs_gen_files.open("test_map.md", "w") as handle:
    handle.write(test_map.generate_test_map())
