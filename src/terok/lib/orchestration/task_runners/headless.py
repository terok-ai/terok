# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Headless (unattended) task runner.

``task_run_headless`` creates a fresh task and runs an agent to
completion in a detached container; ``task_followup_headless`` sends a
follow-up prompt to a completed/failed headless task and restarts it.
The ``HeadlessRunRequest`` / ``DetachedSummary`` value objects bundle
their parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from terok.lib.integrations.executor import (
    AgentConfigSpec,
    prepare_agent_config_dir,
    resolve_instructions,
    resolve_provider_value,
)
from terok.lib.integrations.sandbox import Sharing, VolumeSpec

from ...core import runtime as _rt
from ...core.images import project_cli_image, require_agent_installed
from ...core.projects import load_project
from ...util.ansi import (
    blue as _blue,
    green as _green,
    red as _red,
    supports_color as _supports_color,
)
from ...util.host_cmd import WORKSPACE_DANGEROUS_DIRNAME
from ...util.yaml import load as _yaml_load
from ..agent_config import resolve_agent_config
from ..container_exec import container_git_diff
from ..environment import build_task_env_and_volumes, project_mounts_dir
from ..hooks import run_hook
from ..tasks import (
    CONTAINER_TEROK_CONFIG,
    container_name,
    load_task_meta,
    task_new,
    update_task_exit_code,
    write_task_meta,
)
from .config import _apply_unrestricted_env, _str_to_bool
from .container import _assert_running, _podman_start, _run_container
from .shield import _apply_shield_policy

#: Agent-config volume filename holding the current follow-up prompt.
_PROMPT_FILENAME = "prompt.txt"

#: Agent-config volume filename the prior prompt is archived to on follow-up.
_PROMPT_HISTORY_FILENAME = "prompt-history.txt"


@dataclass(frozen=True)
class HeadlessRunRequest:
    """Groups all parameters for a headless (unattended) agent run."""

    project_id: str
    prompt: str
    config_path: str | None = None
    model: str | None = None
    max_turns: int | None = None
    timeout: int | None = None
    follow: bool = True
    agents: list[str] | None = None
    preset: str | None = None
    name: str | None = None
    provider: str | None = None
    instructions: str | None = None
    unrestricted: bool | None = None


@dataclass(frozen=True)
class DetachedSummary:
    """Groups all parameters for the detached task summary block."""

    label: str
    task_id: str
    cname: str
    color: bool
    log_cmd: str
    stop_cmd: str


def _print_detached_summary(summary: DetachedSummary) -> None:
    """Print the summary block shown after detaching from a headless/follow-up task."""
    print(
        f"\n{summary.label}"
        f"\n- Task:  {summary.task_id}"
        f"\n- Name:  {_green(summary.cname, summary.color)}"
        f"\n- Logs:  {_blue(summary.log_cmd, summary.color)}"
        f"\n- Stop:  {_red(summary.stop_cmd, summary.color)}\n"
    )


def _print_run_summary(project_id: str, task_id: str, mode: str, workspace: Path) -> None:
    """Print a summary of changes made by the headless agent.

    Runs ``git diff --stat`` **inside** the task container to avoid executing
    potentially poisoned git hooks on the host.
    """
    diff_stat = container_git_diff(project_id, task_id, mode, "--stat", "HEAD@{1}..HEAD")
    if diff_stat is not None:
        stripped = diff_stat.strip()
        if stripped:
            print("\n── Changes ──────────────────────────────")
            print(stripped)
        else:
            print("\n── No changes committed ──────────────────")
    print(f"  Workspace: {workspace}")


def _build_cli_overrides(config_path: str | None) -> dict:
    """Load the optional ``--config`` agent-config file into an overrides dict.

    Returns ``{}`` when no config file was given; raises ``SystemExit``
    when the named file does not exist.
    """
    if not config_path:
        return {}
    config_src = Path(config_path)
    if not config_src.is_file():
        raise SystemExit(f"Agent config file not found: {config_path}")
    return _yaml_load(config_src.read_text(encoding="utf-8")) or {}


def _report_headless_result(
    *,
    project_id: str,
    task_id: str,
    cname: str,
    task_dir: Path,
    follow: bool,
    label: str,
    detached_label: str,
) -> None:
    """Print the shared tail of the headless run + follow-up runners.

    When *follow* is set, wait for the container, print the run summary,
    and record the exit code; otherwise print the detached-task block.
    """
    color_enabled = _supports_color()
    if follow:
        exit_code = _rt.resolve_runtime(load_project(project_id)).container(cname).wait()
        _print_run_summary(project_id, task_id, "run", task_dir / WORKSPACE_DANGEROUS_DIRNAME)
        update_task_exit_code(project_id, task_id, exit_code)
        if exit_code != 0:
            print(f"\n{label} exited with code {_red(str(exit_code), color_enabled)}")
    else:
        _print_detached_summary(
            DetachedSummary(
                label=detached_label,
                task_id=task_id,
                cname=cname,
                color=color_enabled,
                log_cmd=f"podman logs -f {cname}",
                stop_cmd=f"podman stop {cname}",
            )
        )


def task_run_headless(request: HeadlessRunRequest) -> str:
    """Run an agent headlessly (unattended mode) in a new task container.

    Creates a new task, prepares the agent-config directory with the provider's
    wrapper function and filtered subagents, then launches a detached container
    that runs init-ssh-and-repo.sh followed by the agent command.

    Args:
        request: All per-run options bundled in a [`HeadlessRunRequest`][terok.lib.orchestration.task_runners.HeadlessRunRequest].

    Returns the task_id.
    """
    from terok.lib.integrations.executor import (
        CLIOverrides,
        get_provider,
    )

    project = load_project(request.project_id)
    resolved = get_provider(request.provider, default_agent=project.default_agent)
    require_agent_installed(project, resolved.name)

    # Resolve layered agent config (global → project → preset → CLI overrides)
    cli_overrides = _build_cli_overrides(request.config_path)
    effective = resolve_agent_config(
        request.project_id,
        agent_config=project.agent_config,
        project_root=project.root,
        preset=request.preset,
        cli_overrides=cli_overrides or None,
    )

    # Resolve instructions: CLI --instructions overrides config stack
    instr_text = (
        request.instructions
        if request.instructions is not None
        else resolve_instructions(effective, resolved.name, project_root=project.root)
    )

    # Apply provider-aware config resolution with best-effort feature mapping.
    # CLI flags override config values; unsupported features produce warnings
    # or prompt augmentation.
    pcfg = resolved.apply_config(
        effective,
        CLIOverrides(
            model=request.model,
            max_turns=request.max_turns,
            timeout=request.timeout,
            instructions=instr_text,
        ),
    )

    # Print warnings about unsupported features
    for warning in pcfg.warnings:
        print(f"Warning: {warning}")

    # Augment prompt with best-effort feature analogues (e.g. max-turns guidance)
    effective_prompt = request.prompt
    if pcfg.prompt_extra:
        effective_prompt = f"{request.prompt}\n\n{pcfg.prompt_extra}"

    # Create a new task
    task_id = task_new(request.project_id, name=request.name)

    # Collect subagents from resolved config
    subagents = tuple(effective.get("subagents") or ())

    # Prepare agent-config dir with wrapper, agents.json, prompt.txt, instructions.md
    task_dir = project.tasks_root / str(task_id)
    agent_config_dir = prepare_agent_config_dir(
        AgentConfigSpec(
            tasks_root=project.tasks_root,
            task_id=task_id,
            subagents=subagents,
            selected_agents=tuple(request.agents) if request.agents is not None else None,
            prompt=effective_prompt,
            provider=resolved.name,
            instructions=instr_text,
            default_agent=project.default_agent,
            mounts_base=project_mounts_dir(project),
        )
    )

    # Resolve unrestricted mode: CLI flag → config → default (True)
    unrestricted = request.unrestricted
    if unrestricted is None:
        cfg_val = resolve_provider_value("unrestricted", effective, resolved.name)
        unrestricted = _str_to_bool(cfg_val) if cfg_val is not None else True

    # Build env and volumes
    env, volumes = build_task_env_and_volumes(project, task_id)

    # Set TEROK_UNRESTRICTED for the wrapper functions inside the container
    if unrestricted:
        _apply_unrestricted_env(env)

    # Mount agent-config dir to /home/dev/.terok
    volumes.append(VolumeSpec(agent_config_dir, CONTAINER_TEROK_CONFIG, sharing=Sharing.PRIVATE))

    # Build headless command via provider registry
    headless_cmd = resolved.build_headless_command(
        timeout=pcfg.timeout,
        model=pcfg.model,
        max_turns=pcfg.max_turns,
    )

    # Build podman command (DETACHED)
    cname = container_name(project.id, "run", task_id)

    meta, meta_path = load_task_meta(project.id, task_id)
    run_hook(
        "pre_start",
        project.hook_pre_start,
        project_id=project.id,
        task_id=task_id,
        mode="run",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        task_id=task_id,
        task_dir=task_dir,
        command=["bash", "-lc", headless_cmd],
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=False)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="run",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    # Update task metadata
    meta["mode"] = "run"
    meta["ready_at"] = datetime.now(UTC).isoformat()
    meta["provider"] = resolved.name
    meta["unrestricted"] = unrestricted
    if request.preset:
        meta["preset"] = request.preset
    write_task_meta(meta_path, meta)

    _report_headless_result(
        project_id=project.id,
        task_id=task_id,
        cname=cname,
        task_dir=task_dir,
        follow=request.follow,
        label=resolved.label,
        detached_label=f"Headless {resolved.label} task started (detached).",
    )

    return task_id


def _inject_followup_prompt(
    *, is_sealed: bool, cname: str, agent_config_dir: Path, prompt: str
) -> None:
    """Hand the follow-up *prompt* to the (stopped) task container.

    Sealed projects get it copied straight in via ``podman cp``; unsealed
    ones replace ``prompt.txt`` on the agent-config volume, archiving the
    prior prompt to ``prompt-history.txt`` so the agent only ever sees
    the current instruction.
    """
    if is_sealed:
        from terok.lib.integrations.executor import inject_prompt

        inject_prompt(cname, prompt)
        return

    prompt_path = agent_config_dir / _PROMPT_FILENAME
    history_path = agent_config_dir / _PROMPT_HISTORY_FILENAME
    existing = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    if existing:
        with history_path.open("a", encoding="utf-8") as hf:
            hf.write(f"{existing}\n\n---\n\n")
    prompt_path.write_text(prompt, encoding="utf-8")


def task_followup_headless(
    project_id: str,
    task_id: str,
    prompt: str,
    follow: bool = True,
) -> None:
    """Send a follow-up prompt to a completed/failed headless task.

    Replaces prompt.txt with the new prompt (so the agent only sees the
    current instruction) and archives the previous content to
    ``prompt-history.txt``.  Restarts the stopped container via
    ``podman start``.  Session context is
    automatically restored for providers that support it:

    - **Claude**: resumes via ``--resume <session-id>`` (captured by a
      ``SessionStart`` hook that writes ``claude-session.txt``).
    - **OpenCode / Blablador**: resumes via ``--session <id>`` (captured by
      the ``opencode-session-plugin.mjs`` plugin that writes the session
      file on ``session.created`` events).
    - **Vibe**: resumes via ``--resume <id>`` (session ID parsed post-run
      from ``~/.vibe/logs/session/`` metadata).
    - **Codex / Copilot**: no session resume support — follow-ups start a
      fresh session with the new prompt only.

    Per-run flags (model, max_turns, timeout) carry forward from the
    original ``task_run_headless`` invocation since ``podman start``
    re-executes the same container command.
    """
    from terok.lib.integrations.executor import AGENT_PROVIDERS

    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id)

    mode = meta.get("mode")
    if mode != "run":
        raise SystemExit(
            f"Task {task_id} is not a headless task (mode={mode!r}). "
            f"Follow-up is only supported for unattended (mode='run') tasks."
        )

    cname = container_name(project.id, "run", task_id)
    container_state = _rt.resolve_runtime(project).container(cname).state
    if container_state == "running":
        raise SystemExit(
            f"Container {cname} is still running. "
            f"Wait for it to finish or stop it before sending a follow-up."
        )
    if container_state is None:
        raise SystemExit(
            f"Container {cname} not found. Cannot follow up — the container may have been removed."
        )

    # Resolve provider from task metadata
    provider_name = meta.get("provider", "claude")
    resolved = AGENT_PROVIDERS.get(provider_name)
    if resolved is None:
        import warnings

        warnings.warn(
            f"Unknown provider {provider_name!r} in task metadata; session resume check skipped.",
            stacklevel=2,
        )
    label = resolved.label if resolved else provider_name

    if resolved and not resolved.supports_session_resume:
        print(
            f"Note: {label} does not support session resume. "
            f"Follow-up will start a fresh session with the new prompt."
        )

    task_dir = project.tasks_root / str(task_id)
    _inject_followup_prompt(
        is_sealed=project.is_sealed,
        cname=cname,
        agent_config_dir=task_dir / "agent-config",
        prompt=prompt,
    )

    # Restart the existing container (re-runs the original bash command,
    # which reads prompt.txt and session files from the volume)
    _podman_start(project, cname)
    _assert_running(project, cname)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="run",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=True)

    # Clear previous exit_code so effective_status shows "running" until new exit
    meta["exit_code"] = None
    write_task_meta(meta_path, meta)

    _report_headless_result(
        project_id=project.id,
        task_id=task_id,
        cname=cname,
        task_dir=task_dir,
        follow=follow,
        label=label,
        detached_label="Follow-up started (detached).",
    )


__all__ = [
    "DetachedSummary",
    "HeadlessRunRequest",
    "task_followup_headless",
    "task_run_headless",
]
