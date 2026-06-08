# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Declarative wizard schema shared by the CLI prompt loop and the TUI modal.

The wizard asks a fixed set of questions to build a new project config.
Declaring them as [`Question`][terok.lib.domain.wizards.new_project.Question] records keeps two presenters — the
CLI's sequential prompts and the TUI's multi-field form — using one
source of truth: same labels, same validation, same transforms.

A presenter's only job is to elicit a raw string per question.  The
shared [`validate_answer`][terok.lib.domain.wizards.new_project.validate_answer] then normalises it, runs the question's
validator, and returns either the accepted value or an error the
presenter can display.  When every question has an accepted answer, the
collected values go to [`generate_config`][terok.lib.domain.wizards.new_project.generate_config], which writes the
``project.yml`` template and returns the path.

[`collect_wizard_inputs`][terok.lib.domain.wizards.new_project.collect_wizard_inputs] is the CLI presenter (uses ``input()``);
the TUI presenter lives in [`terok.tui.wizard_screens`][terok.tui.wizard_screens].
"""

from __future__ import annotations

import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import jinja2
from terok_util import ensure_dir_writable

from terok.lib.integrations.sandbox import check_gpu_available
from terok.ui_utils.editor import open_in_editor

from ...core.config import user_projects_dir
from ...core.project_model import validate_project_name

# ── Vocabulary ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Choice:
    """One selectable option for a ``choice``/``multichoice`` question.

    A non-empty [`disabled_reason`][terok.lib.domain.wizards.new_project.Choice.disabled_reason]
    means the option is offered for visibility (so the user sees what
    *could* be available) but cannot be selected — presenters grey it
    out and [`validate_answer`][terok.lib.domain.wizards.new_project.validate_answer]
    rejects the slug with the reason as the error.
    """

    slug: str
    label: str
    disabled_reason: str = ""


# The wizard picks a project template by asking two independent
# questions (security mode + base image) instead of one combinatorial
# menu.  Template files on disk follow ``{security}-{base}.yml``.
SECURITY_CLASSES: tuple[Choice, ...] = (
    Choice("online", "Online (agent pushes directly to upstream)"),
    Choice("gatekeeping", "Gatekeeping (changes staged for human review)"),
)
BASES: tuple[Choice, ...] = tuple(
    sorted(
        (
            Choice("ubuntu", "Ubuntu 24.04"),
            Choice("fedora", "Fedora 44"),
            Choice("podman", "Podman (Fedora-based)"),
            Choice("nvidia", "NVIDIA CUDA (GPU)"),
        ),
        key=lambda c: c.label.casefold(),
    )
)
BASE_IMAGES: dict[str, str] = {
    "ubuntu": "ubuntu:24.04",
    "fedora": "fedora:44",
    "podman": "quay.io/podman/stable:latest",
    "nvidia": "nvcr.io/nvidia/nvhpc:25.9-devel-cuda13.0-ubuntu24.04",
}

#: Shown next to the NVIDIA base when ``check_gpu_available()`` returns
#: ``False`` — short enough to fit inline beside the option label, with a
#: link to the same docs page [`GpuConfigError.hint`][terok_sandbox.GpuConfigError]
#: surfaces post-launch so users see one consistent pointer either way.
_NVIDIA_UNAVAILABLE_REASON = (
    "NVIDIA Container Device Interface not detected — install the NVIDIA Container Toolkit "
    "and configure CDI first.  See: https://podman-desktop.io/docs/podman/gpu"
)


def _load_base_choices() -> tuple[Choice, ...]:
    """Return the base-image options, tagging ``nvidia`` as disabled when CDI is missing.

    Probing podman lives outside the schema so the wizard surfaces the
    same hint the runtime would emit if the user proceeded blind: see
    [`check_gpu_available`][terok_sandbox.check_gpu_available] and the
    on-launch [`GpuConfigError`][terok_sandbox.GpuConfigError] path.
    """
    if check_gpu_available():
        return BASES
    return tuple(
        Choice(c.slug, c.label, _NVIDIA_UNAVAILABLE_REASON) if c.slug == "nvidia" else c
        for c in BASES
    )


_TEMPLATE_DIR: Traversable = resources.files("terok") / "resources" / "templates" / "projects"
_TEMPLATE_NAME = "project.yml.template"


# ── Question declarations ─────────────────────────────────────────────

QuestionKind = Literal["choice", "text", "editor", "multichoice"]


@dataclass(frozen=True)
class Question:
    """One wizard prompt — what to ask, how to validate, what shape the answer takes.

    The presenter decides the visual treatment (numbered menu vs radio
    buttons, ``input()`` vs Textual ``Input``, ``$EDITOR`` vs ``TextArea``);
    the declaration here drives everything else.
    """

    key: str
    """Name of this field in the collected-values dict."""

    kind: QuestionKind
    """Shape of the input — drives which widget / prompt style a presenter uses."""

    prompt: str
    """Short one-line question, used as both CLI prompt and TUI label."""

    help: str = ""
    """Longer explanation, rendered next to the input in the TUI; unused in CLI."""

    choices: tuple[Choice, ...] = ()
    """Static option list for ``kind in {"choice", "multichoice"}``."""

    choices_loader: Callable[[], tuple[Choice, ...]] | None = None
    """Runtime resolver for choices that aren't known at import time.

    Set this when the option set lives in a sibling wheel (e.g. the
    agent roster) or depends on a host probe (e.g. NVIDIA CDI presence)
    and can drift between calls.  When set, takes precedence over
    [`choices`][terok.lib.domain.wizards.new_project.Question.choices].
    """

    required: bool = False
    """Reject empty answers with ``"<prompt> is required."``"""

    transform: Callable[[str], str] | None = None
    """Optional normalisation applied before validation (e.g. ``str.lower``)."""

    validate: Callable[[str], str | None] | None = None
    """Optional validator returning an error string or ``None`` when accepted."""

    placeholder: str = ""
    """Hint string, rendered inside the Textual ``Input``; unused in CLI."""

    default_visible: bool = False
    """When True, CLI prompt shows ``"(optional)"`` to telegraph "Enter is fine"."""

    def resolve_choices(self) -> tuple[Choice, ...]:
        """Return the effective option list — runtime loader wins over static.

        Called by both presenters whenever they need to render or validate
        a ``choice`` / ``multichoice`` question.  The loader is expected
        to be cheap; the executor's roster lookup is itself ``lru_cache``'d
        so repeated calls are free.
        """
        return self.choices_loader() if self.choices_loader is not None else self.choices


def _validate_project_name(project_name: str) -> str | None:
    """Return an error message if *project_name* is invalid, else ``None``."""
    if not project_name:
        return "Project name cannot be empty."
    try:
        validate_project_name(project_name)
    except SystemExit as exc:
        return str(exc)
    return None


_SLUG_ALLOWED = re.compile(r"[a-z0-9_-]+")
_SLUG_RUNS = re.compile(r"-{2,}")


def _slugify_project_name(raw: str) -> str:
    """Best-effort-normalise *raw* into a valid project name.

    Meets users halfway: ``"terok pages"`` → ``"terok-pages"`` rather than
    bouncing them back with a regex error.  Drops characters outside the
    project name alphabet (``[a-z0-9_-]``), collapses runs of hyphens, and
    strips leading/trailing punctuation.  When the input is already
    hopeless (e.g. ``"!!!"``) the result is empty and validation gives
    the user the usual "must start with a lowercase letter…" message.
    """
    lowered = raw.casefold()
    # Whitespace → single hyphen before dropping out-of-alphabet chars so
    # word boundaries survive ("terok pages" shouldn't glue into "terokpages").
    hyphenated = re.sub(r"\s+", "-", lowered)
    kept = "".join(_SLUG_ALLOWED.findall(hyphenated))
    collapsed = _SLUG_RUNS.sub("-", kept)
    return collapsed.strip("-_")


# ── Agent roster — choices/validator resolved lazily ─────────────────
#
# The roster lives in the ``terok-executor`` sibling wheel and can grow
# between releases.  Wrapping it in a callable keeps the wizard schema
# importable without paying the roster load up front and lets the choice
# list reflect whatever wheel is installed at runtime.

#: Literal selector accepted by [`AgentRoster.parse_selection`][terok_executor.AgentRoster.parse_selection]
#: meaning "every installable roster entry, plus any added later".  Distinct
#: from a comma-list that happens to enumerate all current agents — that
#: list freezes the snapshot, ``"all"`` does not.  (The name avoids
#: ``_TOKEN`` so Sonar's S2068 secret-name heuristic doesn't flag the
#: short literal as a possible hardcoded password.)
_AGENTS_ALL = "all"


def _load_agent_choices() -> tuple[Choice, ...]:
    """Return one [`Choice`][terok.lib.domain.wizards.new_project.Choice] per roster ``kind: agent`` entry.

    The "all" pseudo-option is purely a presenter concern — it lives in
    the TUI's master checkbox and the CLI's prompt default, not in this
    list.  Including it here would force every callsite to filter it out.
    """
    from terok.lib.integrations.executor import AgentRoster

    roster = AgentRoster.shared()
    return tuple(
        Choice(name, roster.agents[name].label if name in roster.agents else name)
        for name in roster.agent_names
    )


def _validate_agents(value: str) -> str | None:
    """Reject unknown agent names; accept ``"all"`` and any comma-list of slugs.

    Defers to the executor's canonical grammar so the wizard speaks the
    same dialect as ``terok image build --agents …``:
    [`AgentRoster.parse_selection`][terok_executor.AgentRoster.parse_selection]
    folds the raw string into ``"all"`` or a token tuple, and
    [`AgentRoster.resolve_selection`][terok_executor.AgentRoster.resolve_selection]
    raises ``ValueError`` with a "Unknown roster entries: …" message when
    a token doesn't match the installed roster.  Excludes (``"-vibe"``)
    and the combined form (``"all,-vibe"``) come for free.
    """
    from terok.lib.integrations.executor import AgentRoster

    try:
        roster = AgentRoster.shared()
        roster.resolve_selection(roster.parse_selection(value))
    except ValueError as exc:
        return str(exc)
    return None


QUESTIONS: tuple[Question, ...] = (
    Question(
        key="security_class",
        kind="choice",
        prompt="Select security mode",
        choices=SECURITY_CLASSES,
        required=True,
    ),
    Question(
        key="base",
        kind="choice",
        prompt="Select base image",
        choices_loader=_load_base_choices,
        required=True,
    ),
    Question(
        key="project_name",
        kind="text",
        prompt="Project name",
        required=True,
        transform=_slugify_project_name,
        validate=_validate_project_name,
        placeholder="lowercase; letters, digits, hyphens, underscores",
    ),
    Question(
        key="upstream_url",
        kind="text",
        prompt="Upstream git URL",
        help="Leave empty for a local-only project (no remote).",
        placeholder="git@github.com:org/repo.git or https://…",
        default_visible=True,
    ),
    Question(
        key="default_branch",
        kind="text",
        prompt="Default branch",
        help="Leave empty to use the remote's default (or ``main`` when no remote).",
        placeholder="main",
        default_visible=True,
    ),
    Question(
        key="user_snippet",
        kind="editor",
        prompt="Custom image snippet",
        help=(
            "Optional Dockerfile fragment appended to the project image.  "
            "Use for extra packages, env vars, or setup commands."
        ),
        default_visible=True,
    ),
    Question(
        key="credentials_scope",
        kind="choice",
        prompt="Authentication credentials for this project",
        help=(
            "Shared (default) reuses the host-wide credential bucket every "
            "project sees — no separate authentication needed.  Project "
            "creates an isolated set: agent logins, OAuth tokens, and shared "
            "config files live under this project's own state directory and "
            "have to be authenticated from scratch via "
            "``terok auth --project <name>``."
        ),
        choices=(
            Choice("shared", "Use shared host-wide credentials (recommended)"),
            Choice("project", "Create an isolated set for this project"),
        ),
        required=True,
    ),
)


#: Agent-roster question gated behind an explicit opt-in (CLI y/N prompt or
#: TUI button) so first-time users land on the global default instead of being
#: forced to pick a roster up front.  Outside [`QUESTIONS`][terok.lib.domain.wizards.new_project.QUESTIONS]
#: because the wizard's main loop does not ask it unconditionally.
AGENTS_QUESTION = Question(
    key="agents",
    kind="multichoice",
    prompt="Select agents to install",
    help=(
        "Which AI coding agents to bake into this project's image, "
        "overriding the global default.  Pick 'All agents' to inherit "
        "future additions, or enumerate specific agents to freeze the set."
    ),
    required=True,
    choices_loader=_load_agent_choices,
    validate=_validate_agents,
)


def validate_answer(question: Question, raw: str) -> tuple[str, str | None]:
    """Normalise and validate a raw answer for *question*.

    Returns ``(value, error_or_None)`` — the normalised value and an
    error message if the answer was rejected.  Both presenters call
    this so validation semantics stay identical regardless of UI.

    Normalisation, in order:

    1. Strip surrounding whitespace (copy-paste leftovers, accidental
       trailing spaces).  All-whitespace input is indistinguishable
       from empty for the required/optional check.
    2. Apply ``question.transform`` if set (e.g. ``str.lower``).
    3. Enforce the required flag against the final value.
    4. For ``kind="choice"``, the value must be one of the declared
       slugs — defensive against presenter bugs that might submit a
       label, index, or free-form typo.
    5. Run ``question.validate`` for field-specific rules.
    """
    value = raw.strip()
    if question.transform:
        value = question.transform(value)
    if question.required and not value:
        return value, f"{question.prompt} is required."
    if question.kind == "choice" and value:
        choices = question.resolve_choices()
        match = next((c for c in choices if c.slug == value), None)
        if match is None:
            allowed = ", ".join(sorted(c.slug for c in choices))
            return value, f"{question.prompt} must be one of: {allowed}"
        if match.disabled_reason:
            return value, match.disabled_reason
    if question.validate:
        err = question.validate(value)
        if err:
            return value, err
    return value, None


# ── CLI presenter ─────────────────────────────────────────────────────


def _prompt(message: str, default: str = "") -> str:
    """Prompt the user for input with an optional default value."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or default


def _prompt_choice(title: str, options: list[Choice]) -> str | None:
    """Show a numbered menu and return the selected slug, or ``None`` on bad input.

    Disabled options are still listed (so users see what *could* be
    available) but rejected post-selection by
    [`validate_answer`][terok.lib.domain.wizards.new_project.validate_answer].
    The trailing ``[unavailable: …]`` suffix telegraphs the why before
    the user types the number.
    """
    print(f"\n{title}")
    for i, c in enumerate(options, 1):
        suffix = f"  [unavailable: {c.disabled_reason}]" if c.disabled_reason else ""
        print(f"  {i}) {c.label}{suffix}")

    choice = input(f"\nChoice [1-{len(options)}]: ").strip()
    if not choice.isdigit():
        return None
    idx = int(choice) - 1
    if 0 <= idx < len(options):
        return options[idx].slug
    return None


#: Exact text terok writes into the snippet tempfile before handing it to
#: ``$EDITOR``.  Matching this verbatim keeps the trimmer from eating
#: intentional user comments at the top of the file — only *our* boilerplate
#: goes away, any other leading ``#`` lines the user types survive.
_SNIPPET_PREAMBLE = "# Add custom Dockerfile commands below.\n# Empty file = no snippet.\n"


def _prompt_image_snippet() -> str:
    """Optionally open an editor for a custom image snippet.

    Returns the snippet text (may be empty if the user skips or the file is empty).
    """
    answer = input("\nAdd a custom image snippet? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        return ""

    with tempfile.NamedTemporaryFile(
        suffix=".dockerfile", prefix="terok-snippet-", mode="w", delete=False
    ) as tmp:
        tmp.write(_SNIPPET_PREAMBLE)
        tmp_path = Path(tmp.name)

    try:
        if not open_in_editor(tmp_path):
            print("Editor could not be opened. Skipping snippet.", file=sys.stderr)
            return ""
        content = tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)

    return _trim_snippet_preamble(content)


def _trim_snippet_preamble(content: str) -> str:
    """Strip exactly the injected preamble and trailing blanks.

    The earlier implementation pruned every leading comment line, which
    would eat user-intended ``# TODO`` or copyright notices.  We instead
    match `_SNIPPET_PREAMBLE` verbatim — if the user didn't
    remove it, drop it; if they did, leave the rest alone.
    """
    if content.startswith(_SNIPPET_PREAMBLE):
        content = content[len(_SNIPPET_PREAMBLE) :]
    # Trailing blanks stay stripped — meaningful-whitespace policy
    # is the same regardless of whether the preamble was intact.
    return content.rstrip("\n").rstrip()


def _prompt_multichoice(title: str, options: list[Choice]) -> str:
    """Lists *options*, then takes one line of comma-separated tokens (default ``"all"``).

    Discovery without the menu juggling: we print the slug-and-label
    table so the user can see what's available, then accept the
    executor's canonical selection syntax verbatim (``"all"``,
    ``"claude,vibe"``, ``"all,-vibe"``).  Empty input means "all" —
    matches the CLI build flag and what most users want first time.
    """
    print(f"\n{title}")
    for c in options:
        print(f"  · {c.slug}  — {c.label}")
    raw = input("\nType a comma list, or '-name' to exclude [all]: ").strip()
    return raw or _AGENTS_ALL


def _ask_cli(question: Question) -> str | None:
    """Elicit a raw string from the terminal for *question*.

    ``None`` means the user's first interaction was structurally invalid
    for a choice (e.g. non-numeric input to the menu) — the CLI treats
    that as a cancel signal, matching the pre-refactor behaviour.
    """
    match question.kind:
        case "choice":
            return _prompt_choice(question.prompt + ":", list(question.resolve_choices()))
        case "multichoice":
            return _prompt_multichoice(question.prompt + ":", list(question.resolve_choices()))
        case "editor":
            return _prompt_image_snippet()
        case "text":
            # Text prompts allow blank retry; the caller loops until
            # ``validate_answer`` accepts the input.
            return _prompt(
                f"\n{question.prompt}" if question.required else question.prompt,
            )


def collect_wizard_inputs() -> dict | None:
    """Drive the CLI prompt loop for every question in [`QUESTIONS`][terok.lib.domain.wizards.new_project.QUESTIONS].

    Returns a dict keyed by ``Question.key`` when all answers are
    accepted, or ``None`` if the user cancels (Ctrl+C, EOF, or an
    invalid choice-menu selection).

    After the main loop, asks an opt-in for the per-project agent
    override via [`prompt_agent_override`][terok.lib.domain.wizards.new_project.prompt_agent_override]
    — the default path leaves ``image.agents`` unset so projects inherit
    the global ``terok agents set`` value.
    """
    values: dict[str, str] = {}
    try:
        for question in QUESTIONS:
            while True:
                raw = _ask_cli(question)
                if raw is None:
                    # Choice menus return None on structurally bad input,
                    # which the pre-refactor flow treated as cancellation.
                    print(f"Invalid {question.key} selection.", file=sys.stderr)
                    return None
                value, error = validate_answer(question, raw)
                if error is None:
                    if question.transform and value != raw.strip():
                        # Surface the normalisation so the user sees *what*
                        # their answer became (e.g. ``"My Proj"`` → ``"my-proj"``).
                        print(f"Note: {question.prompt.lower()} normalised to '{value}'")
                    values[question.key] = value
                    break
                print(error, file=sys.stderr)
        agents = prompt_agent_override()
        if agents:
            values["agents"] = agents
        return values
    except (KeyboardInterrupt, EOFError):
        print("\nWizard cancelled.")
        return None


def prompt_agent_override() -> str:
    """Two-stage opt-in for the per-project agents override.

    Asks ``Override default agents for this project? [y/N]``; on yes,
    runs the multichoice picker and returns the validated selection.
    Returns ``""`` when the user declines — caller leaves
    ``image.agents`` unset so the project inherits the global default.
    """
    answer = input("\nOverride default agents for this project? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        return ""
    while True:
        raw = _prompt_multichoice(
            AGENTS_QUESTION.prompt + ":",
            list(AGENTS_QUESTION.resolve_choices()),
        )
        value, error = validate_answer(AGENTS_QUESTION, raw)
        if error is None:
            return value
        print(error, file=sys.stderr)


# ── Config rendering ──────────────────────────────────────────────────


def generate_config(values: dict) -> Path:
    """Render the project template and write ``project.yml``.

    *values* is the dict returned by [`collect_wizard_inputs`][terok.lib.domain.wizards.new_project.collect_wizard_inputs].
    Returns the path to the created ``project.yml`` file.
    """
    rendered = render_project_yaml(values)

    project_dir = user_projects_dir() / values["project_name"]
    ensure_dir_writable(project_dir, "Project")

    config_path = project_dir / "project.yml"

    if config_path.exists():
        try:
            while True:
                answer = (
                    input(
                        f"Configuration for project '{values['project_name']}' already exists "
                        f"at {config_path}. Overwrite? [y/N]: "
                    )
                    .strip()
                    .lower()
                )
                if answer in ("", "n", "no"):
                    print("Keeping existing configuration; no file was overwritten.")
                    return config_path
                if answer in ("y", "yes"):
                    break
                print("Please answer 'y' or 'n'.")
        except (KeyboardInterrupt, EOFError):
            print("\nKeeping existing configuration; no file was overwritten.")
            return config_path

    config_path.write_text(rendered, encoding="utf-8")
    return config_path


def render_project_yaml(values: dict) -> str:
    """Render ``project.yml`` without writing it — used by the TUI review screen."""
    variables = {
        "PROJECT_NAME": values["project_name"],
        "UPSTREAM_URL": values["upstream_url"],
        "DEFAULT_BRANCH": values["default_branch"],
        "USER_SNIPPET": values["user_snippet"],
        "SECURITY_CLASS": values["security_class"],
        "BASE": values["base"],
        "BASE_IMAGE": BASE_IMAGES[values["base"]],
        # Empty string suppresses the ``agents:`` line via the
        # template's ``{% if AGENTS %}`` gate — the project then
        # inherits the global default written by ``terok agents set``.
        "AGENTS": values.get("agents", ""),
        # Default ``"shared"`` is omitted from the rendered YAML by the
        # template — it matches the runtime default, so writing it back
        # would just add noise to every freshly-created project file.
        "CREDENTIALS_SCOPE": values.get("credentials_scope", "shared"),
    }
    with resources.as_file(_TEMPLATE_DIR / _TEMPLATE_NAME) as template_path:
        # ``StrictUndefined`` upgrades silent ``{{TYPO}}`` to a hard
        # error; ``autoescape=False`` because YAML output would be
        # corrupted by HTML escaping.  The wizard's template uses
        # ``{% if %}`` blocks and the ``| indent`` filter — Jinja2
        # control flow, not just ``{{VAR}}`` substitution.
        env = jinja2.Environment(  # nosec B701 — see comment above  # noqa: S701
            loader=jinja2.FileSystemLoader(str(template_path.parent)),
            keep_trailing_newline=True,
            undefined=jinja2.StrictUndefined,
            autoescape=False,
        )
        return env.get_template(template_path.name).render(**variables)


def write_project_yaml(project_name: str, rendered: str, *, overwrite: bool = False) -> Path:
    """Write *rendered* YAML to ``<user_projects_dir>/<project_name>/project.yml``.

    The TUI reviews YAML in a ``TextArea`` before writing, so this is the
    write half of [`generate_config`][terok.lib.domain.wizards.new_project.generate_config] — kept separate so the TUI can
    pass tweaked content without re-rendering the template.
    """
    project_dir = user_projects_dir() / project_name
    ensure_dir_writable(project_dir, "Project")
    config_path = project_dir / "project.yml"
    if config_path.exists() and not overwrite:
        return config_path
    config_path.write_text(rendered, encoding="utf-8")
    return config_path


# ── CLI edit-and-init follow-up ───────────────────────────────────────


def offer_edit_then_init(
    config_path: Path,
    project_name: str,
    init_fn: Callable[[str], None] | None,
) -> None:
    """Interactively review and commission a newly-created project configuration.

    Opens the config in the user's editor (skippable), then offers to run the
    initialisation routine.  On ``KeyboardInterrupt`` or ``EOFError`` the
    half-finished sequence is abandoned cleanly — the config file is kept
    and a manual next-step hint is printed so the user can resume later.
    """
    try:
        edit_answer = input("Edit configuration file before setup? [Y/n]: ").strip().lower()
        if edit_answer not in ("n", "no") and not open_in_editor(config_path):
            print(
                f"Warning: could not open editor — edit file manually: {config_path}",
                file=sys.stderr,
            )

        if init_fn is not None:
            init_answer = input("Run project initialization? [Y/n]: ").strip().lower()
            if init_answer not in ("n", "no"):
                init_fn(project_name)
                print(f"\nProject '{project_name}' is ready.")
                return

        print(f"Next step: terok project init {project_name}")
    except (KeyboardInterrupt, EOFError):
        print(f"\nSkipped. Run manually: terok project init {project_name}")


def run_wizard(init_fn: Callable[[str], None] | None = None) -> Path | None:
    """Top-level wizard entry point called by the CLI.

    *init_fn* is an optional callable accepting a project name string that
    performs project initialisation (ssh-init, generate, build, gate-sync).
    When ``None`` (the default), no automatic initialisation is offered.

    Returns the path to the generated config file, or ``None`` on cancellation.
    """
    print("=== terok project wizard ===")
    values = collect_wizard_inputs()
    if values is None:
        return None

    config_path = generate_config(values)
    project_name = values["project_name"]
    print(f"\nProject configuration created: {config_path}")

    _maybe_print_global_agents_hint(values)
    offer_edit_then_init(config_path, project_name, init_fn)
    return config_path


def _maybe_print_global_agents_hint(values: dict) -> None:
    """Nudge with ``terok agents set`` when neither scope configures agents.

    Silent when the project overrode the default *or* the global is
    already set — both states already produce a deliberate roster.
    """
    if values.get("agents"):
        return
    try:
        from terok.lib.integrations.executor import ExecutorConfigView
    except ImportError:  # pragma: no cover — executor adapter is always present in shipped builds
        return
    if ExecutorConfigView.image_agents():
        return
    print(
        "\nTip: no default agents are configured.  "
        "Run `terok agents set` to pick the roster baked into L1 by default.",
    )


__all__ = [
    "AGENTS_QUESTION",
    "BASES",
    "Choice",
    "QUESTIONS",
    "Question",
    "QuestionKind",
    "SECURITY_CLASSES",
    "collect_wizard_inputs",
    "generate_config",
    "offer_edit_then_init",
    "prompt_agent_override",
    "render_project_yaml",
    "run_wizard",
    "validate_answer",
    "write_project_yaml",
]
