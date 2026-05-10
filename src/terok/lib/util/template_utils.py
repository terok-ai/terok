# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Render bundled resource templates with Jinja2.

Wraps a single configured ``jinja2.Environment`` so every caller renders
templates the same way: ``StrictUndefined`` (a typo in a placeholder
raises at render time instead of silently leaking ``{{TYPO}}`` into the
output) and ``keep_trailing_newline`` (preserves the file-final newline
that ``Path.read_text()`` returns).

The default ``{{ var }}`` delimiters are byte-compatible with the
previous ``str.replace("{{KEY}}", value)`` mechanism, so existing
template files (``*.template``, project ``*.yml``, the ``.desktop``
launcher) need no syntax migration — they parse identically under
Jinja2.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def render_template(template_path: Path, variables: dict) -> str:
    """Render *template_path* with *variables* using the project Jinja2 env.

    A new ``Environment`` rooted at the template's parent directory is
    created per call so includes/extends (if ever added) resolve
    relative to the template itself.  This keeps the public signature —
    ``(path, dict) -> str`` — identical to the prior simple-replace
    implementation, so callers don't change.

    Raises:
        jinja2.UndefinedError: A placeholder in the template is not
            present in *variables*.  Surfacing typos at render time is
            intentional; the previous implementation silently left the
            literal ``{{KEY}}`` in the output, which usually only became
            visible once a downstream consumer (e.g. Docker, gnome-shell)
            barfed on it.
    """
    # autoescape would HTML-encode <, >, &, " in YAML / Dockerfile /
    # .desktop output and corrupt the rendered files.  These templates
    # never render to HTML; XSS is not in scope.
    env = Environment(  # nosec B701  # noqa: S701
        loader=FileSystemLoader(template_path.parent),
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,
    )
    template = env.get_template(template_path.name)
    return template.render(**variables)
