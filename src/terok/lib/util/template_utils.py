# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Render bundled resource templates with Jinja2.

The default ``{{ var }}`` delimiters are byte-compatible with the
``{{KEY}}`` syntax existing template files use, so the switch from
``str.replace()`` is transparent to callers and template authors.
``StrictUndefined`` upgrades silent typos (``{{TYPO}}`` was previously
left in the rendered output) to a hard `jinja2.UndefinedError` at
render time.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def render_template(template_path: Path, variables: dict) -> str:
    """Render *template_path* with *variables*."""
    # autoescape would HTML-encode <, >, &, " in YAML / Dockerfile /
    # .desktop output and corrupt the rendered files; XSS is not in
    # scope for these templates.
    env = Environment(  # nosec B701  # noqa: S701
        loader=FileSystemLoader(template_path.parent),
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,
    )
    return env.get_template(template_path.name).render(**variables)
