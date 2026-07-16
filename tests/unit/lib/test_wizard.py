# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the interactive new-project wizard."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from terok.lib.domain.wizards.new_project import (
    AGENTS_QUESTION,
    BASES,
    DEFAULT_BASE,
    QUESTIONS,
    SECURITY_CLASSES,
    GpuDeviceChoice,
    Question,
    _slugify_project_name,
    _validate_project_name,
    collect_wizard_inputs,
    detect_gpu_choices,
    generate_config,
    prompt_agent_override,
    prompt_custom_image,
    prompt_gpu_passthrough,
    render_project_yaml,
    run_wizard,
    validate_answer,
    validate_custom_image,
    validate_gpus,
    write_project_yaml,
)
from tests.testfs import mock_wizard_project_file


@pytest.fixture(autouse=True)
def _gpu_prompt_off() -> object:
    """Silence the CLI GPU opt-in so input sequences stay one-per-question.

    The dedicated GPU tests below exercise the real prompt themselves.
    """
    with patch("terok.lib.domain.wizards.new_project.prompt_gpu_passthrough", return_value="") as p:
        yield p


def wizard_values(
    *,
    security_class: str = "online",
    base: str = "fedora",
    project_name: str = "test-proj",
    upstream_url: str = "https://github.com/user/repo.git",
    default_branch: str = "main",
    agents: str | None = None,
    user_snippet: str = "",
    credentials_scope: str = "shared",
    gpus: str = "",
) -> dict[str, object]:
    """Build a wizard value dict with sensible defaults."""
    values: dict[str, object] = {
        "security_class": security_class,
        "base": base,
        "project_name": project_name,
        "upstream_url": upstream_url,
        "default_branch": default_branch,
        "user_snippet": user_snippet,
        "credentials_scope": credentials_scope,
        "gpus": gpus,
    }
    if agents is not None:
        values["agents"] = agents
    return values


@pytest.mark.parametrize(
    ("project_name", "valid"),
    [
        pytest.param("myproject", True, id="simple"),
        pytest.param("my-project", True, id="hyphen"),
        pytest.param("my_project", True, id="underscore"),
        pytest.param("proj123", True, id="digits"),
        pytest.param("my-project_2", True, id="mixed-lowercase"),
        pytest.param("My-Project_2", False, id="uppercase"),
        pytest.param("", False, id="empty"),
        pytest.param("my project", False, id="spaces"),
        pytest.param("my@project", False, id="special-chars"),
        pytest.param("-myproject", False, id="starts-with-hyphen"),
        pytest.param("_myproject", False, id="starts-with-underscore"),
        pytest.param("default", False, id="reserved-default"),
    ],
)
def test_validate_project_name(project_name: str, valid: bool) -> None:
    """Project name validation accepts only the supported slug-like IDs."""
    error = _validate_project_name(project_name)
    assert (error is None) is valid


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        pytest.param(
            # sec, base, pid, upstream, branch, snippet-y/N, creds-scope, override-agents-y/N
            ["1", "3", "myproj", "https://example.com/r.git", "main", "n", "1", "n"],
            wizard_values(project_name="myproj", upstream_url="https://example.com/r.git"),
            id="collect-all-values-no-override",
        ),
        pytest.param(
            ["2", "3", "gkproj", "git@host:r.git", "", "n", "1", "n"],
            wizard_values(
                security_class="gatekeeping",
                project_name="gkproj",
                upstream_url="git@host:r.git",
                default_branch="",
            ),
            id="gatekeeping-selection",
        ),
        pytest.param(
            ["1", "5", "proj", "https://example.com/r.git", "", "n", "1", "n"],
            wizard_values(
                base="nvidia",
                project_name="proj",
                upstream_url="https://example.com/r.git",
                default_branch="",
            ),
            id="empty-default-branch",
        ),
        pytest.param(
            # ... snippet=n, creds-scope=project, override-agents=y, then a comma-list selection
            [
                "1",
                "5",
                "proj",
                "https://example.com/r.git",
                "dev",
                "n",
                "2",
                "y",
                "claude,vibe",
            ],
            wizard_values(
                base="nvidia",
                project_name="proj",
                upstream_url="https://example.com/r.git",
                default_branch="dev",
                agents="claude,vibe",
                credentials_scope="project",
            ),
            id="opt-in-agents-override",
        ),
        pytest.param(
            ["1", "3", "!!!", "good-id", "https://example.com/r.git", "main", "n", "1", "n"],
            wizard_values(project_name="good-id", upstream_url="https://example.com/r.git"),
            id="retry-invalid-project-name",
        ),
        pytest.param(
            ["1", "3", "proj", "", "main", "n", "1", "n"],
            wizard_values(project_name="proj", upstream_url=""),
            id="empty-upstream-url-accepted",
        ),
    ],
)
def test_collect_wizard_inputs_success(
    inputs: list[str],
    expected: dict[str, object],
) -> None:
    """Wizard input collection retries invalid inputs and returns normalized values."""
    with patch("builtins.input", side_effect=inputs):
        assert collect_wizard_inputs() == expected


@pytest.mark.parametrize(
    "side_effect",
    [
        pytest.param(["invalid"], id="invalid-mode"),
        pytest.param(["0"], id="mode-below-range"),
        pytest.param(["9"], id="mode-above-range"),
        pytest.param(["1", "invalid"], id="invalid-base"),
        pytest.param(["1", "9"], id="base-above-range"),
        pytest.param(KeyboardInterrupt, id="ctrl-c"),
        pytest.param(EOFError, id="eof"),
    ],
)
def test_collect_wizard_inputs_cancellation_paths(
    side_effect: list[str] | type[BaseException],
) -> None:
    """Invalid menu selection or user cancellation returns ``None``."""
    with patch("builtins.input", side_effect=side_effect):
        assert collect_wizard_inputs() is None


def test_collect_wizard_inputs_lowercases_project_name() -> None:
    """Uppercase project names are normalised with a friendly note."""
    with (
        patch(
            "builtins.input",
            side_effect=["1", "3", "MyProject", "https://example.com/r.git", "main", "", "1", "n"],
        ),
        patch("builtins.print") as mock_print,
    ):
        result = collect_wizard_inputs()

    assert result == wizard_values(
        project_name="myproject", upstream_url="https://example.com/r.git"
    )
    printed = [" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list]
    assert any("normalised to 'myproject'" in line for line in printed)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("terok", "terok", id="already-valid-passthrough"),
        pytest.param("My Project", "my-project", id="spaces-to-hyphens"),
        pytest.param("MyProject", "myproject", id="camelcase-lowercased"),
        pytest.param("proj!!@#", "proj", id="special-chars-dropped"),
        pytest.param("foo   ---   bar", "foo-bar", id="runs-collapsed"),
        pytest.param("   edgy   ", "edgy", id="surrounding-whitespace-stripped"),
        pytest.param("-_proj_-", "proj", id="leading-and-trailing-punctuation-trimmed"),
        pytest.param("!!!", "", id="nothing-salvageable"),
        pytest.param("terok pages", "terok-pages", id="the-field-report-case"),
    ],
)
def test_slugify_project_name(raw: str, expected: str) -> None:
    """``_slugify_project_name`` meets users halfway without silently mangling intent."""
    assert _slugify_project_name(raw) == expected


def generate_into_tmp(values: dict[str, object]) -> tuple[str, str, str]:
    """Generate a project config into a temporary user-projects root and return path metadata."""
    with tempfile.TemporaryDirectory() as td:
        with patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)):
            config_path = generate_config(values)
            return (
                config_path.name,
                config_path.parent.name,
                config_path.read_text(encoding="utf-8"),
            )


@pytest.mark.parametrize(
    ("values", "expected_snippets"),
    [
        pytest.param(
            wizard_values(),
            [
                'name: "test-proj"',
                "https://github.com/user/repo.git",
                'default_branch: "main"',
                'security_class: "online"',
            ],
            id="online-default",
        ),
        pytest.param(
            wizard_values(
                security_class="gatekeeping",
                project_name="gk-proj",
                upstream_url="git@github.com:user/repo.git",
                default_branch="dev",
                user_snippet="RUN apt-get update",
            ),
            [
                'security_class: "gatekeeping"',
                'default_branch: "dev"',
                "RUN apt-get update",
                "gatekeeping:",
            ],
            id="gatekeeping-default",
        ),
        pytest.param(
            wizard_values(
                base="nvidia",
                gpus="nvidia",
                project_name="gpu-proj",
                upstream_url="https://example.com/r.git",
            ),
            ['gpus: "nvidia"', "nvcr.io/nvidia/"],
            id="online-nvidia",
        ),
        pytest.param(
            wizard_values(
                gpus="amd:1,intel",
                project_name="devsel-proj",
                upstream_url="https://example.com/r.git",
            ),
            ['gpus: "amd:1,intel"'],
            id="device-selector-any-base",
        ),
        pytest.param(
            wizard_values(
                base="fedora",
                project_name="fedora-proj",
                upstream_url="https://example.com/r.git",
            ),
            ['base_image: "fedora:44"', 'security_class: "online"'],
            id="online-fedora",
        ),
        pytest.param(
            wizard_values(
                base="podman",
                project_name="podman-proj",
                upstream_url="https://example.com/r.git",
            ),
            ['base_image: "quay.io/podman/stable:latest"'],
            id="online-podman",
        ),
    ],
)
def test_generate_config_templates(values: dict[str, object], expected_snippets: list[str]) -> None:
    """Generated configs include the expected template-specific content."""
    config_name, project_dir_name, content = generate_into_tmp(values)
    assert config_name == "project.yml"
    assert project_dir_name == values["project_name"]
    for snippet in expected_snippets:
        assert snippet in content


def test_generate_config_replaces_all_placeholders() -> None:
    """All template placeholders are rendered away for every (mode, base) pair."""
    for sec in SECURITY_CLASSES:
        for base in BASES:
            _, _, content = generate_into_tmp(
                wizard_values(
                    security_class=sec.slug,
                    base=base.slug,
                    project_name=f"proj-{sec.slug}-{base.slug}",
                    upstream_url="https://example.com/r.git",
                    user_snippet="RUN echo hi",
                )
            )
            for placeholder in (
                "{{PROJECT_NAME}}",
                "{{UPSTREAM_URL}}",
                "{{DEFAULT_BRANCH}}",
                "{{USER_SNIPPET}}",
                "{{AGENTS}}",
            ):
                assert placeholder not in content, f"{sec.slug}-{base.slug}: {placeholder}"


@pytest.mark.parametrize(
    (
        "collect_result",
        "user_answers",
        "has_init_fn",
        "editor_success",
        "expect_init",
        "expect_result",
    ),
    [
        pytest.param(
            wizard_values(project_name="proj1", upstream_url="https://example.com/r.git"),
            ["y", "y"],
            True,
            True,
            True,
            mock_wizard_project_file("proj1"),
            id="edit-and-init",
        ),
        pytest.param(
            wizard_values(project_name="proj2", upstream_url="https://example.com/r.git"),
            ["n", "n"],
            True,
            True,
            False,
            mock_wizard_project_file("proj2"),
            id="skip-edit-and-init",
        ),
        pytest.param(
            wizard_values(project_name="proj3", upstream_url="https://example.com/r.git"),
            ["n"],
            False,
            True,
            False,
            mock_wizard_project_file("proj3"),
            id="no-init-fn",
        ),
        pytest.param(
            wizard_values(project_name="proj4", upstream_url="https://example.com/r.git"),
            KeyboardInterrupt,
            False,
            True,
            False,
            mock_wizard_project_file("proj4"),
            id="cancel-after-generate",
        ),
        pytest.param(None, [], False, True, False, None, id="collect-cancelled"),
    ],
)
def test_run_wizard(
    collect_result: dict[str, object] | None,
    user_answers: list[str] | type[BaseException],
    has_init_fn: bool,
    editor_success: bool,
    expect_init: bool,
    expect_result: Path | None,
) -> None:
    """Wizard orchestration handles edit/init prompts and cancellation paths."""
    init_fn = Mock() if has_init_fn else None
    with (
        patch(
            "terok.lib.domain.wizards.new_project.collect_wizard_inputs",
            return_value=collect_result,
        ),
        patch(
            "terok.lib.domain.wizards.new_project.generate_config", return_value=expect_result
        ) as mock_generate_config,
        patch(
            "terok.lib.domain.wizards.new_project.open_in_editor", return_value=editor_success
        ) as mock_editor,
        patch("builtins.input", side_effect=user_answers),
    ):
        result = run_wizard(init_fn=init_fn)

    assert result == expect_result
    if collect_result is None:
        mock_generate_config.assert_not_called()
        mock_editor.assert_not_called()
        return

    mock_generate_config.assert_called_once_with(collect_result)

    if user_answers is KeyboardInterrupt:
        mock_editor.assert_not_called()
    else:
        assert mock_editor.call_count == (
            0 if user_answers and user_answers[0] in {"n", "no"} else 1
        )
    if expect_init:
        init_fn.assert_called_once_with(collect_result["project_name"])
    elif init_fn is not None:
        init_fn.assert_not_called()


# ---------------------------------------------------------------------------
# validate_answer — spec surface shared by the CLI loop and the TUI modal.
# Parametrised so presenter tests can lean on this as the source of truth
# for per-field behaviour.
# ---------------------------------------------------------------------------


def _q(key: str) -> Question:
    """Look up the declared question for *key* — fails fast on drift."""
    if key == AGENTS_QUESTION.key:
        return AGENTS_QUESTION
    for q in QUESTIONS:
        if q.key == key:
            return q
    raise AssertionError(f"No question with key {key!r} in QUESTIONS")


class TestValidateAnswer:
    """validate_answer covers every branch a presenter would need to handle."""

    def test_choice_accepts_declared_slug(self) -> None:
        """A raw slug from choices passes through unchanged."""
        value, err = validate_answer(_q("security_class"), "online")
        assert value == "online"
        assert err is None

    def test_required_rejects_empty(self) -> None:
        """A required question refuses empty input with the standard message."""
        value, err = validate_answer(_q("project_name"), "")
        assert err is not None
        assert "required" in err

    def test_optional_accepts_empty(self) -> None:
        """An optional question (upstream_url) is fine with an empty answer."""
        value, err = validate_answer(_q("upstream_url"), "")
        assert value == ""
        assert err is None

    def test_transform_runs_before_validation(self) -> None:
        """Slugify + lowercase on project_name normalises before the regex check fires."""
        value, err = validate_answer(_q("project_name"), "MyProject")
        assert value == "myproject"
        assert err is None

    def test_transform_slugifies_spaces_and_specials(self) -> None:
        """Whitespace turns into hyphens and stray punctuation is dropped."""
        value, err = validate_answer(_q("project_name"), "My Fancy Project!!")
        assert value == "my-fancy-project"
        assert err is None

    def test_transform_collapses_hyphen_runs(self) -> None:
        """Consecutive delimiters collapse into one hyphen."""
        value, err = validate_answer(_q("project_name"), "foo   ---   bar")
        assert value == "foo-bar"
        assert err is None

    def test_validator_surfaces_error(self) -> None:
        """A slug that can't be salvaged by slugification surfaces the regex error."""
        # Only punctuation — nothing in the allowed alphabet survives.
        value, err = validate_answer(_q("project_name"), "!!!")
        assert err is not None
        assert "Invalid project name" in err or "required" in err

    def test_editor_kind_accepts_arbitrary_text(self) -> None:
        """Editor-style questions have no validator; any string goes through."""
        snippet = "RUN apt-get update && apt-get install -y ripgrep"
        value, err = validate_answer(_q("user_snippet"), snippet)
        assert value == snippet
        assert err is None

    def test_surrounding_whitespace_is_stripped(self) -> None:
        """Leading/trailing whitespace is removed before validation and transform."""
        value, err = validate_answer(_q("project_name"), "  MyProj  ")
        assert value == "myproj"
        assert err is None

    def test_all_whitespace_answer_counts_as_empty_for_required(self) -> None:
        """``'   '`` → required field rejected the same way an empty string is."""
        value, err = validate_answer(_q("project_name"), "   ")
        assert value == ""
        assert err is not None
        assert "required" in err

    def test_unknown_choice_slug_is_rejected(self) -> None:
        """Defensive check: a bogus slug for a choice question returns an error."""
        value, err = validate_answer(_q("security_class"), "bogus")
        assert err is not None
        assert "must be one of" in err

    def test_whitespace_only_optional_text_accepted_as_empty(self) -> None:
        """Optional text field: spaces collapse to empty and pass through cleanly."""
        value, err = validate_answer(_q("upstream_url"), "   ")
        assert value == ""
        assert err is None


# ---------------------------------------------------------------------------
# GPU passthrough — opt-in prompt, selector validation, device detection.
# GPU software-stack bases are never gated on hardware: the image is a
# software choice, useful with or without a GPU on this host.
# ---------------------------------------------------------------------------


class TestBaseQuestion:
    """The base question is a defaulted dropdown with all bases selectable."""

    def test_dropdown_with_default(self) -> None:
        base_q = next(q for q in QUESTIONS if q.key == "base")
        assert base_q.dropdown is True
        assert base_q.default == DEFAULT_BASE

    def test_gpu_bases_always_selectable(self) -> None:
        for c in BASES:
            assert c.disabled_reason == ""


class TestCustomBase:
    """The bring-your-own-image option: prompt, validation, rendering."""

    def test_prompt_retries_until_valid(self) -> None:
        with patch("builtins.input", side_effect=["", "has space", "rockylinux:9"]):
            assert prompt_custom_image() == "rockylinux:9"

    @pytest.mark.parametrize("value", ["rockylinux:9", "registry.example.com:5000/org/img:tag"])
    def test_validate_accepts(self, value: str) -> None:
        assert validate_custom_image(value) is None

    @pytest.mark.parametrize("value", ["", "two words", 'rocky"linux:9', "img\\name", "img'x"])
    def test_validate_rejects(self, value: str) -> None:
        """Empty, whitespace, and YAML-hostile quote characters all bounce."""
        assert validate_custom_image(value) is not None

    def test_render_uses_custom_image_and_family_hint(self) -> None:
        values = wizard_values(base="custom", project_name="cust")
        values["custom_image"] = "rockylinux:9"
        rendered = render_project_yaml(values)
        assert 'base_image: "rockylinux:9"' in rendered
        assert "# family: rpm" in rendered
        assert "custom-images" in rendered

    def test_bundled_bases_carry_no_family_hint(self) -> None:
        rendered = render_project_yaml(wizard_values(project_name="plain", upstream_url=""))
        assert "# family:" not in rendered


class TestGpuPrompt:
    """``prompt_gpu_passthrough`` — default off, base-vendor default, retry."""

    def test_default_off(self) -> None:
        with patch("builtins.input", side_effect=[""]):
            assert prompt_gpu_passthrough("ubuntu") == ""

    def test_opt_in_defaults_all(self) -> None:
        with (
            patch("builtins.input", side_effect=["y", ""]),
            patch("terok.lib.domain.wizards.new_project.detect_gpu_choices", return_value=()),
        ):
            assert prompt_gpu_passthrough("ubuntu") == "all"

    def test_gpu_base_defaults_vendor_on(self) -> None:
        """A GPU software-stack base flips the default to on with its vendor."""
        with (
            patch("builtins.input", side_effect=["", ""]),
            patch("terok.lib.domain.wizards.new_project.detect_gpu_choices", return_value=()),
        ):
            assert prompt_gpu_passthrough("rocm") == "amd"

    def test_gpu_base_still_declinable(self) -> None:
        """Software-only use of a GPU base: answer no, get no gpus block."""
        with patch("builtins.input", side_effect=["n"]):
            assert prompt_gpu_passthrough("nvidia") == ""

    def test_invalid_selector_retries(self) -> None:
        with (
            patch("builtins.input", side_effect=["y", "matrox", "amd:1"]),
            patch("terok.lib.domain.wizards.new_project.detect_gpu_choices", return_value=()),
        ):
            assert prompt_gpu_passthrough("ubuntu") == "amd:1"


class TestValidateGpus:
    """``validate_gpus`` defers to sandbox's selector grammar."""

    @pytest.mark.parametrize("value", ["", "all", "nvidia", "amd:1,intel", "nvidia:0,nvidia:1"])
    def test_accepts(self, value: str) -> None:
        assert validate_gpus(value) is None

    @pytest.mark.parametrize("value", ["matrox", "nvidia:x", "all:0"])
    def test_rejects(self, value: str) -> None:
        assert validate_gpus(value) is not None


class TestDetectGpuChoices:
    """Detection maps vendors/devices to picker entries; failure → ()."""

    def test_multi_device_vendor_gets_indexed_tokens(self) -> None:
        with (
            patch(
                "terok.lib.domain.wizards.new_project.detect_gpu_vendors",
                return_value=frozenset({"amd", "intel"}),
            ),
            patch(
                "terok.lib.domain.wizards.new_project.gpu_device_addresses",
                return_value={
                    "nvidia": (),
                    "amd": ("0000:03:00.0", "0000:0b:00.0"),
                    "intel": ("0000:00:02.0",),
                },
            ),
        ):
            choices = detect_gpu_choices()
        assert [c.token for c in choices] == ["amd:0", "amd:1", "intel"]
        assert "0000:0b:00.0" in choices[1].label

    def test_detection_failure_is_empty(self) -> None:
        with patch(
            "terok.lib.domain.wizards.new_project.detect_gpu_vendors",
            side_effect=RuntimeError("no podman"),
        ):
            assert detect_gpu_choices() == ()

    def test_choice_type_is_stable(self) -> None:
        assert GpuDeviceChoice("amd", "amd (0000:03:00.0)").token == "amd"


# ---------------------------------------------------------------------------
# render_project_yaml / write_project_yaml — TUI-only rendering helpers that
# need the same template resolution as generate_config.
# ---------------------------------------------------------------------------


class TestRenderAndWrite:
    """The two-halves split of generate_config used by the TUI review path."""

    def test_render_project_yaml_matches_generate_output(self) -> None:
        """In-memory render must equal the file generate_config writes."""
        values = wizard_values(project_name="renderp", upstream_url="https://example.com/r.git")
        rendered = render_project_yaml(values)
        with tempfile.TemporaryDirectory() as td:
            with patch(
                "terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)
            ):
                path = generate_config(values)
            assert path.read_text(encoding="utf-8") == rendered

    def test_write_project_yaml_refuses_overwrite_by_default(self) -> None:
        """A second write without ``overwrite=True`` leaves the original in place."""
        with (
            tempfile.TemporaryDirectory() as td,
            patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)),
        ):
            first = write_project_yaml("scratch", "first: true\n")
            second = write_project_yaml("scratch", "second: true\n")
            assert first == second
            assert first.read_text() == "first: true\n"

    def test_write_project_yaml_overwrite_true_replaces_contents(self) -> None:
        """``overwrite=True`` replaces the contents — used by the TUI review path."""
        with (
            tempfile.TemporaryDirectory() as td,
            patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)),
        ):
            write_project_yaml("scratch", "first: true\n")
            path = write_project_yaml("scratch", "second: true\n", overwrite=True)
            assert path.read_text() == "second: true\n"


# ---------------------------------------------------------------------------
# QUESTIONS registry — ordering and shape invariants the presenters rely on.
# ---------------------------------------------------------------------------


class TestQuestionsRegistry:
    """Guard against accidental drift in the wizard vocabulary."""

    def test_declared_keys_unique(self) -> None:
        keys = [q.key for q in QUESTIONS]
        assert len(keys) == len(set(keys))

    def test_first_two_are_choice_questions(self) -> None:
        """Template filename is ``{security}-{base}.yml`` — both must be choice."""
        assert QUESTIONS[0].key == "security_class"
        assert QUESTIONS[0].kind == "choice"
        assert QUESTIONS[1].key == "base"
        assert QUESTIONS[1].kind == "choice"

    def test_every_choice_has_non_empty_options(self) -> None:
        for q in QUESTIONS:
            if q.kind == "choice":
                assert q.resolve_choices(), f"{q.key} has empty choices"

    def test_agents_question_lives_outside_questions(self) -> None:
        """The agents prompt is gated and presented separately — not part of the main loop."""
        assert all(q.key != "agents" for q in QUESTIONS)

    def test_agents_question_has_runtime_loader(self) -> None:
        """The gated agents question resolves the roster at render time."""
        assert AGENTS_QUESTION.kind == "multichoice"
        assert AGENTS_QUESTION.choices_loader is not None
        assert AGENTS_QUESTION.resolve_choices()  # loader returns the live roster


# ---------------------------------------------------------------------------
# Agents question — multichoice grammar delegated to terok_executor.
# These tests pin the contract the wizard relies on: "all" + comma-list +
# exclude tokens accepted, unknown slugs rejected with the executor's
# canonical error message, empty input rejected by ``required``.
# ---------------------------------------------------------------------------


class TestAgentsQuestion:
    """Validation contract shared by the CLI prompt loop and the TUI form."""

    def test_all_token_accepted(self) -> None:
        value, err = validate_answer(_q("agents"), "all")
        assert err is None
        assert value == "all"

    def test_comma_list_of_known_agents_accepted(self) -> None:
        from terok.lib.integrations.executor import AgentRoster

        first, second = AgentRoster.shared().agent_names[:2]
        raw = f"{first},{second}"
        value, err = validate_answer(_q("agents"), raw)
        assert err is None
        assert value == raw

    def test_exclude_syntax_accepted(self) -> None:
        """``all,-name`` is the executor's canonical exclude form — must flow through."""
        from terok.lib.integrations.executor import AgentRoster

        omit = AgentRoster.shared().agent_names[0]
        value, err = validate_answer(_q("agents"), f"all,-{omit}")
        assert err is None
        assert value == f"all,-{omit}"

    def test_unknown_agent_rejected_with_helpful_message(self) -> None:
        value, err = validate_answer(_q("agents"), "claude,definitely-not-an-agent")
        assert err is not None
        assert "definitely-not-an-agent" in err

    def test_empty_required(self) -> None:
        """Empty submit (e.g. TUI master + all items unchecked) must be rejected."""
        value, err = validate_answer(_q("agents"), "")
        assert err is not None
        assert "required" in err

    def test_render_emits_agents_line_quoted(self) -> None:
        """Template must quote the agents scalar so future roster names can't break YAML."""
        rendered = render_project_yaml(wizard_values(agents="claude,vibe"))
        assert 'agents: "claude,vibe"' in rendered

    def test_render_round_trips_through_yaml_safe_load(self) -> None:
        """The rendered ``project.yml`` parses; ``agents`` is the string we set."""
        import yaml

        rendered = render_project_yaml(wizard_values(agents="all"))
        parsed = yaml.safe_load(rendered)
        assert parsed["image"]["agents"] == "all"

    def test_render_omits_agents_line_when_unset(self) -> None:
        """No override → no ``agents:`` key; the project inherits the global default."""
        import yaml

        rendered = render_project_yaml(wizard_values())  # agents=None default
        parsed = yaml.safe_load(rendered)
        assert "agents" not in parsed["image"]
        # Sanity: the commented-out hint pointing at the new commands.
        assert "terok agents set" in rendered
        assert "terok project agents set" in rendered


# ---------------------------------------------------------------------------
# prompt_agent_override — the two-stage gate the CLI wizard uses
# ---------------------------------------------------------------------------


class TestPromptAgentOverride:
    """Behaviour of the gated agents prompt that runs after the main wizard loop."""

    def test_returns_empty_when_user_declines(self) -> None:
        """Pressing Enter (or 'n') at the gate skips the multichoice and returns ''."""
        with patch("builtins.input", side_effect=[""]):
            assert prompt_agent_override() == ""

    def test_returns_all_on_empty_multichoice(self) -> None:
        """Opting in then pressing Enter at the multichoice means ``"all"``."""
        with patch("builtins.input", side_effect=["y", ""]):
            assert prompt_agent_override() == "all"

    def test_returns_comma_list(self) -> None:
        """Opt-in followed by a comma list returns that list verbatim."""
        from terok.lib.integrations.executor import AgentRoster

        names = AgentRoster.shared().agent_names
        if len(names) < 2:
            pytest.skip("Need at least 2 agents in roster")
        raw = f"{names[0]},{names[1]}"
        with patch("builtins.input", side_effect=["yes", raw]):
            assert prompt_agent_override() == raw

    def test_retries_on_invalid_selection(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Invalid agent name re-prompts the multichoice; the error is surfaced."""
        with patch("builtins.input", side_effect=["y", "claude,definitely-not-an-agent", "all"]):
            assert prompt_agent_override() == "all"
        # Validator's error message routes through stderr.
        assert "definitely-not-an-agent" in capsys.readouterr().err


class TestGlobalAgentsHint:
    """The post-wizard hint fires only when neither scope configures agents."""

    def test_hint_printed_when_both_scopes_unset(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No project override + no global default → hint surfaces to stdout."""
        from terok.lib.domain.wizards.new_project import _maybe_print_global_agents_hint

        with patch(
            "terok.lib.integrations.executor.ExecutorConfigView.image_agents",
            return_value=None,
        ):
            _maybe_print_global_agents_hint({})
        out = capsys.readouterr().out
        assert "terok agents set" in out

    def test_hint_skipped_when_project_overrides(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Per-project override → no need to nudge about the global default."""
        from terok.lib.domain.wizards.new_project import _maybe_print_global_agents_hint

        with patch(
            "terok.lib.integrations.executor.ExecutorConfigView.image_agents",
            return_value=None,
        ):
            _maybe_print_global_agents_hint({"agents": "claude"})
        assert "terok agents set" not in capsys.readouterr().out

    def test_hint_skipped_when_global_already_set(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Global already configured → no nudge."""
        from terok.lib.domain.wizards.new_project import _maybe_print_global_agents_hint

        with patch(
            "terok.lib.integrations.executor.ExecutorConfigView.image_agents",
            return_value="all",
        ):
            _maybe_print_global_agents_hint({})
        assert "terok agents set" not in capsys.readouterr().out
