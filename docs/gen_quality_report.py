# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate a code quality report page for MkDocs.

This script runs during ``mkdocs build`` via the mkdocs-gen-files plugin.
It executes complexipy, vulture, tach, and docstr-coverage, then assembles
the results into a single Markdown page with a Mermaid dependency diagram.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import mkdocs_gen_files

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "terok"
COMPLEXITY_THRESHOLD = 15
_VENV_BIN = Path(sys.executable).parent

# Depth at which to aggregate modules in the dependency diagram.
# 3 → terok.lib.containers, terok.lib.core, etc.
_GRAPH_DEPTH = 3


def _run(
    *cmd: str, cwd: Path = ROOT, timeout_seconds: float = 120.0
) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result (never raises on failure)."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timed out")


def _section_complexity() -> str:
    """Generate cognitive complexity section from complexipy."""
    # Run complexipy to populate the cache (use CLI entry point, not -m).
    # Note: --quiet is omitted because it causes a spurious non-zero exit code
    # in complexipy >=5.x.  capture_output=True suppresses stdout anyway.
    run_result = _run(str(_VENV_BIN / "complexipy"), str(SRC), "--ignore-complexity")
    if run_result.returncode != 0:
        output = (run_result.stdout + run_result.stderr).strip()
        return f"!!! warning\n    complexipy failed; skipping complexity report.\n\n```\n{output}\n```\n"

    # Find the cache file
    cache_dir = ROOT / ".complexipy_cache"
    cache_files = sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []
    if not cache_files:
        return "!!! warning\n    complexipy cache not found — skipping complexity report.\n"

    latest_cache = max(cache_files, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(latest_cache.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "!!! warning\n    complexipy cache is invalid JSON — skipping complexity report.\n"
    raw_functions = data.get("functions", [])
    functions: list[dict[str, object]] = []
    for item in raw_functions:
        if not isinstance(item, dict):
            continue
        complexity = item.get("complexity")
        if not isinstance(complexity, (int, float)):
            continue
        functions.append(
            {
                "complexity": complexity,
                "function_name": str(item.get("function_name", "<unknown>")),
                "path": str(item.get("path", "<unknown>")),
            }
        )
    if not functions:
        return "No functions found.\n"

    # Sort by complexity descending
    functions.sort(key=lambda f: f["complexity"], reverse=True)

    # Summary stats
    total = len(functions)
    over_threshold = [f for f in functions if f["complexity"] > COMPLEXITY_THRESHOLD]
    max_c = functions[0]["complexity"] if functions else 0
    avg_c = sum(f["complexity"] for f in functions) / total if total else 0

    lines = [
        f"- **Functions analyzed:** {total}\n",
        f"- **Average complexity:** {avg_c:.1f}\n",
        f"- **Max complexity:** {max_c}\n",
        f"- **Exceeding threshold ({COMPLEXITY_THRESHOLD}):** {len(over_threshold)}\n",
        "\n",
    ]

    if over_threshold:
        lines.append("| Complexity | Function | File |\n")
        lines.append("|---:|---|---|\n")
        for f in over_threshold:
            lines.append(f"| {f['complexity']} | `{f['function_name']}` | `{f['path']}` |\n")
    else:
        lines.append(
            f"All functions are within the cognitive complexity threshold of {COMPLEXITY_THRESHOLD}.\n"
        )

    return "".join(lines)


def _section_dead_code() -> str:
    """Generate dead code section from vulture."""
    result = _run(
        sys.executable,
        "-m",
        "vulture",
        str(SRC),
        str(ROOT / "vulture_whitelist.py"),
        "--min-confidence",
        "80",
    )
    output = (result.stdout + result.stderr).strip()
    if not output:
        return "No dead code found at 80% confidence threshold.\n"

    def _md_cell(value: str) -> str:
        return value.replace("|", r"\|").replace("\n", " ")

    lines = ["| Confidence | Location | Issue |\n", "|---:|---|---|\n"]
    for line in output.splitlines():
        # Format: path:line: message (NN% confidence)
        if "% confidence)" in line:
            parts = line.rsplit("(", 1)
            location_msg = parts[0].strip()
            confidence = parts[1].rstrip(")").strip()
            # Split location:line: message
            loc_parts = location_msg.split(": ", 1)
            location = loc_parts[0] if loc_parts else location_msg
            message = loc_parts[1] if len(loc_parts) > 1 else ""
            lines.append(
                f"| {_md_cell(confidence)} | `{_md_cell(location)}` | {_md_cell(message)} |\n"
            )
        else:
            lines.append(f"| — | — | {_md_cell(line)} |\n")
    return "".join(lines)


def _coarsen_module(name: str, depth: int = _GRAPH_DEPTH) -> str:
    """Truncate a dotted module path to *depth* segments."""
    parts = name.split(".")
    return ".".join(parts[:depth])


def _coarsen_graph(mermaid_lines: list[str]) -> list[str]:
    """Aggregate fine-grained mermaid edges into a coarser high-level graph.

    Edges between sub-modules of the same group are dropped.  Duplicate
    coarsened edges are collapsed and annotated with a count.
    """
    edge_re = re.compile(r"^\s*(.+?)\s*-->\s*(.+?)\s*$")
    edge_counts: dict[tuple[str, str], int] = defaultdict(int)
    nodes: set[str] = set()

    for line in mermaid_lines:
        m = edge_re.match(line)
        if not m:
            continue
        src = _coarsen_module(m.group(1).strip())
        dst = _coarsen_module(m.group(2).strip())
        nodes.add(src)
        nodes.add(dst)
        if src != dst:
            edge_counts[(src, dst)] += 1

    # Build the coarsened graph (top-down)
    out = ["graph TD"]
    for (src, dst), count in sorted(edge_counts.items()):
        label = f"|{count}|" if count > 1 else ""
        # Use short aliases to keep the diagram compact
        out.append(f"    {src} -->{label} {dst}")
    # Emit isolated nodes (no outgoing edges)
    connected = {n for pair in edge_counts for n in pair}
    for node in sorted(nodes - connected):
        out.append(f"    {node}")
    return out


def _section_dependency_diagram() -> str:
    """Generate module dependency diagram from tach."""
    result = _run(sys.executable, "-m", "tach", "show", "--mermaid", "-o", "-")
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip() or "no output"
        return (
            f"!!! warning\n    tach show failed (exit {result.returncode}).\n\n```\n{output}\n```\n"
        )
    output = result.stdout.strip()
    if not output:
        return "!!! warning\n    tach show --mermaid produced no output.\n"

    # Extract just the mermaid edges (skip the NOTE lines and the "graph" header)
    edge_lines = []
    in_graph = False
    for line in output.splitlines():
        if line.startswith("graph "):
            in_graph = True
            continue
        if in_graph:
            edge_lines.append(line)

    if not edge_lines:
        return "!!! warning\n    Could not parse mermaid output from tach.\n"

    coarsened = _coarsen_graph(edge_lines)
    return "```mermaid\n" + "\n".join(coarsened) + "\n```\n"


def _section_dependency_report() -> str:
    """Generate dependency report from tach."""
    result = _run(sys.executable, "-m", "tach", "report", str(SRC))
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip() or "no output"
        return f"!!! warning\n    tach report failed (exit {result.returncode}).\n\n```\n{output}\n```\n"
    output = result.stdout.strip()
    if not output:
        return "No dependency report available.\n"
    return (
        "<details>\n<summary>Full dependency report (click to expand)</summary>\n\n"
        f"```\n{output}\n```\n\n"
        "</details>\n"
    )


def _section_boundary_check() -> str:
    """Run tach check and report results."""
    result = _run(sys.executable, "-m", "tach", "check")
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return "All module boundaries validated.\n"
    return f"```\n{output}\n```\n"


def _section_docstring_coverage() -> str:
    """Generate docstring coverage section."""
    result = _run(
        str(_VENV_BIN / "docstr-coverage"),
        str(SRC),
        "--fail-under=0",
    )
    output = (result.stdout + result.stderr).strip()
    # Extract the summary lines
    summary_lines = []
    for line in output.splitlines():
        if any(kw in line for kw in ("Needed:", "Total coverage:", "Grade:")):
            summary_lines.append(f"- {line.strip()}\n")
    if not summary_lines:
        return f"```\n{output}\n```\n"
    return "".join(summary_lines)


def generate_report() -> str:
    """Assemble the full quality report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    sections = [
        "# Code Quality Report\n\n",
        f"*Generated: {now}*\n\n",
        "---\n\n",
        "## Module Dependency Graph\n\n",
        _section_dependency_diagram(),
        "\n",
        "## Module Boundaries\n\n",
        _section_boundary_check(),
        "\n",
        "## Cognitive Complexity\n\n",
        f"Threshold: **{COMPLEXITY_THRESHOLD}** (functions above this are listed below)\n\n",
        _section_complexity(),
        "\n",
        "## Dead Code Analysis\n\n",
        _section_dead_code(),
        "\n",
        "## Docstring Coverage\n\n",
        _section_docstring_coverage(),
        "\n",
        "## Dependency Report\n\n",
        _section_dependency_report(),
        "\n---\n\n",
        "*Generated by complexipy, vulture, tach, and docstr-coverage.*\n",
    ]

    return "".join(sections)


# --- mkdocs-gen-files entry point ---
report = generate_report()
with mkdocs_gen_files.open("quality-report.md", "w") as f:
    f.write(report)
