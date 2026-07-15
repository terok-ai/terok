# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the unified project wizard YAML template."""

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

import jinja2
import pytest
import yaml

from terok.lib.domain.wizards.new_project import BASE_IMAGES, BASES, SECURITY_CLASSES


def _render_template(path: Path, variables: dict) -> str:
    """Inline Jinja2 render with the standard terok defaults."""
    # autoescape off because outputs are YAML/Dockerfile/.desktop, never HTML.
    env = jinja2.Environment(  # noqa: S701 — see comment above
        loader=jinja2.FileSystemLoader(str(path.parent)),
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    return env.get_template(path.name).render(**variables)


TEMPLATE_DIR: Traversable = resources.files("terok") / "resources" / "templates" / "projects"
TEMPLATE_NAME = "project.yml.template"
REQUIRED_PLACEHOLDERS: list[str] = [
    "{{PROJECT_NAME}}",
    "{{UPSTREAM_URL}}",
    "{{DEFAULT_BRANCH}}",
    "{{USER_SNIPPET | indent(4)}}",
    "{{BASE_IMAGE}}",
    "{{AGENTS}}",
    '"{{SECURITY_CLASS}}"',
]


def _full_variables(*, security_class: str, base: str, **overrides: str) -> dict[str, str]:
    """Build a complete variables dict for the unified template."""
    return {
        "PROJECT_NAME": overrides.get("project_name", "my-project"),
        "UPSTREAM_URL": overrides.get("upstream_url", "https://example.test/repo.git"),
        "DEFAULT_BRANCH": overrides.get("default_branch", "main"),
        "USER_SNIPPET": overrides.get("user_snippet", ""),
        "SECURITY_CLASS": security_class,
        "BASE": base,
        "BASE_IMAGE": BASE_IMAGES[base],
        "AGENTS": overrides.get("agents", "all"),
        "CREDENTIALS_SCOPE": overrides.get("credentials_scope", "shared"),
    }


def _render(security_class: str, base: str, **overrides: str) -> str:
    traversable = TEMPLATE_DIR / TEMPLATE_NAME
    with resources.as_file(traversable) as path:
        return _render_template(
            path, _full_variables(security_class=security_class, base=base, **overrides)
        )


class TestProjectTemplate:
    """Tests for the unified project.yml.template."""

    def test_template_file_exists(self) -> None:
        assert (TEMPLATE_DIR / TEMPLATE_NAME).is_file()

    def test_template_contains_required_placeholders(self) -> None:
        content = (TEMPLATE_DIR / TEMPLATE_NAME).read_text(encoding="utf-8")
        for placeholder in REQUIRED_PLACEHOLDERS:
            assert placeholder in content, f"missing placeholder: {placeholder}"

    @pytest.mark.parametrize("security_class", [c.slug for c in SECURITY_CLASSES])
    @pytest.mark.parametrize("base", [c.slug for c in BASES])
    def test_renders_for_every_combination(self, security_class: str, base: str) -> None:
        rendered = _render(security_class, base, project_name=f"proj-{security_class}-{base}")
        # Every placeholder must be substituted away.
        assert "{{" not in rendered
        assert "{%" not in rendered
        # Output must be parseable YAML — Jinja whitespace/trim mistakes
        # would silently produce malformed indentation.
        parsed = yaml.safe_load(rendered)
        assert parsed["project"]["security_class"] == security_class
        assert parsed["image"]["base_image"] == BASE_IMAGES[base]

    def test_gatekeeping_hint_only_for_gatekeeping(self) -> None:
        """The gatekeeping knobs appear as a commented example, never active.

        The runtime defaults are the secure configuration; a freshly
        generated project.yml must not override any of them.
        """
        rendered = _render("gatekeeping", "ubuntu")
        assert "# gatekeeping:" in rendered
        assert "gatekeeping" not in yaml.safe_load(rendered)
        assert "gatekeeping" not in _render("online", "ubuntu")

    def test_gatekeeping_template_never_mentions_posture_degrading_knobs(self) -> None:
        """``expose_external_remote`` (default off) stays out of the template.

        Advertising it — even commented — would nudge users toward
        weakening the gatekeeping isolation; it belongs in the docs.
        """
        assert "expose_external_remote" not in _render("gatekeeping", "ubuntu")

    def test_gate_disable_hint_only_for_online(self) -> None:
        """``gate.enabled: false`` is rejected for gatekeeping projects at
        load time, so only the online template may advertise it."""
        assert "# gate:" in _render("online", "ubuntu")
        assert "# gate:" not in _render("gatekeeping", "ubuntu")

    def test_run_gpus_section_only_for_nvidia(self) -> None:
        assert "gpus: all" in _render("online", "nvidia")
        assert "gpus: all" not in _render("online", "ubuntu")

    def test_agents_line_omitted_when_unset(self) -> None:
        """Empty AGENTS suppresses the line and surfaces the commented hint."""
        rendered = _render("online", "ubuntu", agents="")
        parsed = yaml.safe_load(rendered)
        assert "agents" not in parsed["image"]
        # Hint pointing at the new commands for setting agents on demand.
        assert "terok agents set" in rendered

    def test_agents_line_present_when_set(self) -> None:
        """Non-empty AGENTS produces the ``agents:`` key with the quoted value."""
        rendered = _render("online", "ubuntu", agents="all,-vibe")
        parsed = yaml.safe_load(rendered)
        assert parsed["image"]["agents"] == "all,-vibe"

    def test_renders_user_snippet_inline(self) -> None:
        rendered = _render("online", "ubuntu", user_snippet="RUN apt-get update")
        assert "RUN apt-get update" in rendered

    def test_template_renders_shared_credentials_scope(self) -> None:
        """``credentials_scope='shared'`` must NOT emit a ``credentials:`` block.

        Shared is the runtime default; writing ``credentials.scope: shared``
        to every freshly-generated project.yml would just add noise.
        """
        rendered = _render("online", "ubuntu", credentials_scope="shared")
        parsed = yaml.safe_load(rendered)
        assert "credentials" not in parsed

    def test_template_renders_project_credentials_scope(self) -> None:
        """``credentials_scope='project'`` emits ``credentials: { scope: project }``."""
        rendered = _render("online", "ubuntu", credentials_scope="project")
        parsed = yaml.safe_load(rendered)
        assert parsed["credentials"] == {"scope": "project"}

    def test_multi_line_user_snippet_keeps_block_scalar_valid(self) -> None:
        """Lines 2+ of USER_SNIPPET must inherit the block scalar's indent."""
        snippet = "RUN apt-get update\nRUN apt-get install -y curl\nRUN echo done"
        rendered = _render("online", "ubuntu", user_snippet=snippet)
        # Round-trip through yaml.safe_load to prove the block scalar is valid
        # and the snippet is preserved verbatim.
        parsed = yaml.safe_load(rendered)
        assert parsed["image"]["user_snippet_inline"].rstrip("\n") == snippet

    def test_raises_on_missing_variable(self) -> None:
        """A typo or forgotten variable surfaces at render time, not silently."""
        traversable = TEMPLATE_DIR / TEMPLATE_NAME
        # Drop PROJECT_NAME to trigger the StrictUndefined guard.
        variables = _full_variables(security_class="online", base="ubuntu")
        del variables["PROJECT_NAME"]
        with resources.as_file(traversable) as path, pytest.raises(jinja2.UndefinedError):
            _render_template(path, variables)
