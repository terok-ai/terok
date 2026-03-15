# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for init-ssh-and-repo.sh branch selection behavior.

These tests verify that the init script correctly checks out the branch
specified in GIT_BRANCH rather than the remote's default HEAD.

This is an integration-style test that creates real git repos to test the
actual shell script behavior.
"""

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class RepoLayout:
    """Paths for an init-script integration test workspace."""

    base: Path
    gate_path: Path
    workspace_path: Path
    upstream_path: Path | None = None


def run_git(
    *args: str,
    repo_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with the standard isolated test environment."""
    command = ["git", *([] if repo_path is None else ["-C", str(repo_path)]), *args]
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env if env is not None else get_clean_git_env(),
    )


def get_init_script_path() -> Path:
    """Get the path to init-ssh-and-repo.sh."""
    return REPO_ROOT / "src" / "terok" / "resources" / "scripts" / "init-ssh-and-repo.sh"


def get_clean_git_env(temp_dir: Path | None = None) -> dict:
    """Get an environment dict with git-related variables cleaned.

    This prevents interference from the workspace git config when running tests.

    Args:
        temp_dir: Optional temp dir for git config. If not provided, uses empty config.
    """
    env = os.environ.copy()
    # Remove any git-related env vars that could cause interference
    for key in list(env.keys()):
        if key.startswith("GIT_"):
            del env[key]
    # Disable global/system config by pointing to empty files
    if temp_dir:
        empty_config = temp_dir / ".gitconfig-empty"
        empty_config.touch()
        env["GIT_CONFIG_GLOBAL"] = str(empty_config)
        env["GIT_CONFIG_SYSTEM"] = str(empty_config)
    else:
        # Use HOME to isolate git config
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def create_bare_repo_with_branches(
    repo_path: Path, default_branch: str, other_branches: list[str]
) -> None:
    """Create a bare git repo with multiple branches.

    Args:
        repo_path: Path where to create the bare repo
        default_branch: The branch that HEAD will point to (remote's default)
        other_branches: Additional branches to create
    """
    git_env = get_clean_git_env()

    # Create a temp working repo first
    with tempfile.TemporaryDirectory() as work_dir:
        work_path = Path(work_dir)

        # Initialize with default branch
        run_git("init", "-b", default_branch, str(work_path), env=git_env)
        run_git("config", "user.email", "test@test.com", repo_path=work_path, env=git_env)
        run_git("config", "user.name", "Test", repo_path=work_path, env=git_env)

        # Create initial commit on default branch
        (work_path / "README.md").write_text(f"# {default_branch}\n")
        run_git("add", ".", repo_path=work_path, env=git_env)
        run_git(
            "commit",
            "-m",
            f"Initial commit on {default_branch}",
            repo_path=work_path,
            env=git_env,
        )

        # Create other branches with unique commits
        for branch in other_branches:
            run_git("checkout", "-b", branch, repo_path=work_path, env=git_env)
            (work_path / "README.md").write_text(f"# {branch}\n")
            run_git("add", ".", repo_path=work_path, env=git_env)
            run_git("commit", "-m", f"Commit on {branch}", repo_path=work_path, env=git_env)

        # Switch back to default branch (so HEAD points to it in the bare clone)
        run_git("checkout", default_branch, repo_path=work_path, env=git_env)

        # Clone to bare repo
        run_git("clone", "--bare", str(work_path), str(repo_path), env=git_env)

        # Explicitly set HEAD to the default branch in the bare repo
        # (git clone --bare doesn't always do this correctly)
        run_git(
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{default_branch}",
            repo_path=repo_path,
            env=git_env,
        )


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch of a git repo."""
    return run_git("rev-parse", "--abbrev-ref", "HEAD", repo_path=repo_path).stdout.strip()


def get_file_content(repo_path: Path, filename: str) -> str:
    """Get content of a file in the repo."""
    return (repo_path / filename).read_text().strip()


def file_repo_url(path: Path) -> str:
    """Build a file:// repo URL for the given path."""
    return f"file://{path}"


def make_repo_layout(base: Path, *, with_upstream: bool = False) -> RepoLayout:
    """Create the standard gate/workspace layout for init-script tests."""
    workspace_path = base / "workspace"
    workspace_path.mkdir()
    return RepoLayout(
        base=base,
        gate_path=base / "gate.git",
        workspace_path=workspace_path,
        upstream_path=base / "upstream.git" if with_upstream else None,
    )


def run_layout_init(
    init_script: Path,
    layout: RepoLayout,
    *,
    code_repo: Path | None = None,
    git_branch: str | None = None,
    clone_from: Path | None = None,
    reset_mode: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the init script using the standard layout and optional overrides."""
    env = {
        "CODE_REPO": file_repo_url(code_repo or layout.gate_path),
        "REPO_ROOT": str(layout.workspace_path),
    }
    if git_branch is not None:
        env["GIT_BRANCH"] = git_branch
    if clone_from is not None:
        env["CLONE_FROM"] = file_repo_url(clone_from)
    if reset_mode is not None:
        env["GIT_RESET_MODE"] = reset_mode
    return run_init_script(init_script, layout.base, env)


def get_remote_url(repo_path: Path, remote: str = "origin") -> str:
    """Return a git remote URL from a checked-out repository."""
    return run_git("remote", "get-url", remote, repo_path=repo_path).stdout.strip()


def run_init_script(
    init_script: Path, base_path: Path, extra_env: dict
) -> subprocess.CompletedProcess:
    """Run the init script with a clean environment."""
    # Start with minimal environment to avoid pollution from test runner
    env = {
        "HOME": str(base_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": os.environ.get("TERM", "xterm"),
        # Disable git's global/system config
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    # Add the extra env vars from the test
    env.update(extra_env)
    # Ensure CLONE_FROM is not set unless explicitly provided
    if "CLONE_FROM" not in extra_env:
        env.pop("CLONE_FROM", None)

    return subprocess.run(
        ["bash", str(init_script)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(base_path),
    )


class TestInitScriptBranchSelection:
    """Test that init-ssh-and-repo.sh respects GIT_BRANCH setting."""

    def setup_method(self, _method: object) -> None:
        self.init_script = get_init_script_path()
        if not self.init_script.exists():
            pytest.skip(f"Init script not found at {self.init_script}")

    def test_initial_clone_uses_git_branch_not_remote_default(self) -> None:
        """Test that initial clone checks out GIT_BRANCH, not the remote's default HEAD.

        This is the core bug fix test. Before the fix, cloning from a remote
        (especially file:// URLs in gatekeeping mode) would leave the workspace
        on whatever branch HEAD pointed to in the remote, ignoring GIT_BRANCH.
        """
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))

            # Create gate with 'master' as default HEAD but 'dev' as target branch
            create_bare_repo_with_branches(
                layout.gate_path,
                default_branch="master",  # Remote's HEAD points here
                other_branches=["dev", "feature"],  # We want to checkout 'dev'
            )

            # Run init script with GIT_BRANCH=dev (simulates gatekeeping mode)
            result = run_layout_init(self.init_script, layout, git_branch="dev")

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            # Workspace should be on 'dev', not 'master'
            current_branch = get_current_branch(layout.workspace_path)
            message = (
                f"Expected branch 'dev' but got '{current_branch}'. "
                f"Script should use GIT_BRANCH, not remote's default HEAD.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            assert current_branch == "dev", message

            # Verify we have the dev branch content
            content = get_file_content(layout.workspace_path, "README.md")
            assert content == "# dev"

    def test_initial_clone_falls_back_to_cloned_default_if_branch_missing(self) -> None:
        """Test fallback to cloned default branch when GIT_BRANCH doesn't exist."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))

            create_bare_repo_with_branches(
                layout.gate_path, default_branch="main", other_branches=[]
            )

            result = run_layout_init(self.init_script, layout, git_branch="nonexistent")

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            # Should warn about missing branch
            assert "WARNING" in result.stdout
            assert "nonexistent" in result.stdout

            current_branch = get_current_branch(layout.workspace_path)
            assert current_branch == "main"

    def test_initial_clone_fails_if_workspace_not_empty(self) -> None:
        """Initial clone should fail fast when workspace is pre-populated."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))
            (layout.workspace_path / "stray.txt").write_text("unexpected")

            create_bare_repo_with_branches(
                layout.gate_path, default_branch="master", other_branches=[]
            )

            result = run_layout_init(self.init_script, layout, git_branch="master")

            assert result.returncode != 0, "Script should fail on non-empty workspace"
            combined = f"{result.stdout}\n{result.stderr}"
            assert "is not empty before initial clone" in combined
            assert "branch master not found" not in combined

    def test_initial_clone_removes_marker_and_succeeds(self) -> None:
        """Initial clone should remove the new-task marker using normal init flow."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))
            marker_path = layout.workspace_path / ".new-task-marker"
            marker_path.write_text("marker")

            create_bare_repo_with_branches(
                layout.gate_path, default_branch="master", other_branches=[]
            )

            result = run_layout_init(self.init_script, layout, git_branch="master")

            assert result.returncode == 0, f"Script failed: {result.stderr}"
            assert not marker_path.exists(), "Marker should be removed by init script"
            assert (layout.workspace_path / ".git").exists()

    def test_initial_clone_uses_remote_default_if_git_branch_unset(self) -> None:
        """Test that remote's default branch is used when GIT_BRANCH is not set."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))

            # Create gate with 'master' as default and 'main' also exists
            create_bare_repo_with_branches(
                layout.gate_path,
                default_branch="master",  # Remote HEAD
                other_branches=["main"],
            )

            result = run_layout_init(self.init_script, layout)

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            current_branch = get_current_branch(layout.workspace_path)
            assert current_branch == "master"

    def test_online_mode_with_clone_from_uses_git_branch(self) -> None:
        """Test online mode: clone from gate, repoint to upstream, use GIT_BRANCH.

        In online mode with a gate, CLONE_FROM is set to the gate and CODE_REPO
        is the upstream URL. The script should repoint origin and then checkout
        the branch specified in GIT_BRANCH.
        """
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td), with_upstream=True)
            assert layout.upstream_path is not None

            create_bare_repo_with_branches(
                layout.gate_path, default_branch="master", other_branches=["dev"]
            )

            create_bare_repo_with_branches(
                layout.upstream_path, default_branch="master", other_branches=["dev"]
            )

            result = run_layout_init(
                self.init_script,
                layout,
                code_repo=layout.upstream_path,
                clone_from=layout.gate_path,
                git_branch="dev",
            )

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            current_branch = get_current_branch(layout.workspace_path)
            assert current_branch == "dev"

            origin_url = get_remote_url(layout.workspace_path)
            assert origin_url == file_repo_url(layout.upstream_path)

    def test_new_task_marker_resets_to_git_branch(self) -> None:
        """Test that new task marker triggers reset to GIT_BRANCH."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))

            create_bare_repo_with_branches(
                layout.gate_path, default_branch="master", other_branches=["dev", "feature"]
            )

            run_git("clone", str(layout.gate_path), str(layout.workspace_path))
            assert get_current_branch(layout.workspace_path) == "master"

            marker_path = layout.workspace_path / ".new-task-marker"
            marker_path.write_text("marker")

            result = run_layout_init(self.init_script, layout, git_branch="dev")

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            current_branch = get_current_branch(layout.workspace_path)
            assert current_branch == "dev"

            assert not marker_path.exists()

    def test_restarted_task_preserves_local_branch(self) -> None:
        """Test that restarted task (no marker) preserves local state."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td))

            create_bare_repo_with_branches(
                layout.gate_path, default_branch="master", other_branches=["dev"]
            )

            run_git("clone", str(layout.gate_path), str(layout.workspace_path))
            run_git("checkout", "dev", repo_path=layout.workspace_path)

            (layout.workspace_path / "local.txt").write_text("local change")
            run_git("add", ".", repo_path=layout.workspace_path)

            result = run_layout_init(
                self.init_script,
                layout,
                git_branch="master",
                reset_mode="none",
            )

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            current_branch = get_current_branch(layout.workspace_path)
            assert current_branch == "dev"

            assert (layout.workspace_path / "local.txt").exists()


class TestGatekeepingModeOrigin:
    """Test that gatekeeping mode correctly sets origin to gate."""

    def setup_method(self, _method: object) -> None:
        self.init_script = get_init_script_path()
        if not self.init_script.exists():
            pytest.skip(f"Init script not found at {self.init_script}")

    def test_gatekeeping_mode_fixes_origin_to_gate(self) -> None:
        """Test that origin is always set to gate in gatekeeping mode (file:// URL)."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_repo_layout(Path(td), with_upstream=True)
            assert layout.upstream_path is not None

            create_bare_repo_with_branches(layout.gate_path, "main", [])
            create_bare_repo_with_branches(layout.upstream_path, "main", [])

            run_git("clone", str(layout.upstream_path), str(layout.workspace_path))

            origin_before = get_remote_url(layout.workspace_path)
            assert "upstream" in origin_before

            result = run_layout_init(self.init_script, layout, git_branch="main")

            assert result.returncode == 0, f"Script failed: {result.stderr}"

            origin_after = get_remote_url(layout.workspace_path)
            assert origin_after == file_repo_url(layout.gate_path)
