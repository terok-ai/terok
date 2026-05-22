# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Jinja2-based renderer for terok templates that need control flow.

The simple ``{{KEY}}`` substitution case is owned by
[`terok_util.templates.render_template`][terok_util.templates.render_template]
— strict (rejects control chars in substitution values) and dependency-free
(no Jinja2).  This module stays in terok because the wizard's
``project.yml.template`` uses Jinja2 control flow (``{%- if SECURITY_CLASS
== "online" -%}``) and the ``| indent(4)`` filter for multi-line block
scalars — neither of which the strict-replace variant supports.  Callers
whose templates only use ``{{KEY}}`` substitution should import from
``terok_util`` instead so they pick up the strict char-rejection guard.

The default Jinja2 ``{{ var }}`` delimiters are byte-compatible with the
``{{KEY}}`` syntax existing template files use, so the move from
``str.replace()`` to Jinja2 was transparent to callers and template
authors.  ``StrictUndefined`` upgrades silent typos (``{{TYPO}}`` was
previously left in the rendered output) to a hard `jinja2.UndefinedError`
at render time.
"""

from importlib import resources
from importlib.resources.abc import Traversable
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


def render_resource_template(traversable: Traversable, variables: dict) -> str:
    """Render a packaged-resource template, handling the ``as_file`` dance."""
    with resources.as_file(traversable) as template_path:
        return render_template(template_path, variables)
