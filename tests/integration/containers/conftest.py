# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Session-scoped fixtures that build L0 + shell-init test images via Podman.

Two images are built once per session (~30-60s, no agent installs):

1. **L0** from the real ``l0.dev.Dockerfile.template`` — validates the base layer.
2. **Shell-init layer** on top of L0 using a small Dockerfile that replicates L1's
   shell wiring (COPY terok-env.sh, terok-env-git-identity.sh, mkdir,
   ``/etc/bash.bashrc`` append, ``BASH_ENV``) — validates the exact wiring that
   broke when ``mkdir -p /usr/local/share/terok`` was below the cache bust point.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

ITEST_L0_IMAGE = "terok-itest-l0:latest"
ITEST_SHELL_IMAGE = "terok-itest-shell:latest"

# Shell-init Dockerfile — mirrors the L1 wiring steps that previously broke.
_SHELL_INIT_DOCKERFILE = textwrap.dedent("""\
    ARG BASE
    FROM ${BASE}
    USER root
    RUN mkdir -p /usr/local/share/terok
    COPY scripts/terok-env.sh /etc/profile.d/terok-env.sh
    COPY scripts/terok-env-git-identity.sh /usr/local/share/terok/terok-env-git-identity.sh
    RUN chmod +x /etc/profile.d/terok-env.sh \\
                 /usr/local/share/terok/terok-env-git-identity.sh; \\
        if [ -f /etc/bash.bashrc ]; then \\
          printf '\\n. /etc/profile.d/terok-env.sh\\n' >> /etc/bash.bashrc; \\
        else \\
          printf '. /etc/profile.d/terok-env.sh\\n' > /etc/bash.bashrc; \\
        fi
    ENV BASH_ENV=/etc/profile.d/terok-env.sh
    USER dev
    WORKDIR /workspace
""")


def _copy_resource_tree(package: str, rel_path: str, dest: Path) -> None:
    """Copy a package resource directory tree to a filesystem path."""
    root = resources.files(package) / rel_path

    def _recurse(src, dst: Path) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            out = dst / child.name
            if child.is_dir():
                _recurse(child, out)
            else:
                out.write_bytes(child.read_bytes())

    _recurse(root, dest)


def _podman_build(
    dockerfile: Path,
    tag: str,
    context: Path,
    *,
    build_args: dict[str, str] | None = None,
    timeout: int = 300,
) -> None:
    """Run ``podman build`` with clear error reporting."""
    ba_flags: list[str] = []
    for k, v in (build_args or {}).items():
        ba_flags.extend(["--build-arg", f"{k}={v}"])
    result = subprocess.run(
        ["podman", "build", "-f", str(dockerfile), *ba_flags, "-t", tag, str(context)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"podman build failed for {tag} (exit {result.returncode}):\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  stdout: {result.stdout.strip()}"
        )


@pytest.fixture(scope="session")
def shell_test_image(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Build L0 + shell-init test images and yield the shell image tag.

    Skips if podman is not installed. Both images are removed in the finalizer.
    """
    if not shutil.which("podman"):
        pytest.skip("podman not installed")

    build_dir = tmp_path_factory.mktemp("container-build")

    # Render the L0 template through terok_executor's Jinja pipeline.
    # Reading the raw template and writing it verbatim leaves `{{ BASE_IMAGE }}`
    # and `{% if family ... %}` placeholders un-substituted, so podman build
    # dies at `FROM {{` with "invalid reference format".
    from terok_executor.container.build import render_l0, stage_scripts, stage_tmux_config

    # Fully-qualified base image: nested rootless podman in the matrix images
    # has no unqualified-search-registries, so a short name like ``fedora:44``
    # (executor's default) fails to resolve.
    (build_dir / "L0.Dockerfile").write_text(render_l0("registry.fedoraproject.org/fedora:44"))

    # Stage scripts (executor's own + sandbox bridge overlays) and tmux
    # config into the build context.  Using executor's `stage_scripts()`
    # rather than a bare resources-tree copy is what gets the
    # ssh-agent-bridge.sh / ensure-bridges.sh scripts in — those live in
    # terok_sandbox, and the L0 Dockerfile's COPY lines expect them under
    # scripts/.  Without the overlay, STEP 8 (COPY ssh-agent-bridge.sh)
    # fails with "no such file or directory".
    stage_scripts(build_dir / "scripts")
    stage_tmux_config(build_dir / "tmux")

    # Build L0 from the real template.
    _podman_build(build_dir / "L0.Dockerfile", ITEST_L0_IMAGE, build_dir)

    # Write and build the shell-init layer.
    shell_df = build_dir / "Shell.Dockerfile"
    shell_df.write_text(_SHELL_INIT_DOCKERFILE)
    _podman_build(
        shell_df,
        ITEST_SHELL_IMAGE,
        build_dir,
        build_args={"BASE": ITEST_L0_IMAGE},
        timeout=120,
    )

    yield ITEST_SHELL_IMAGE

    # Cleanup: remove both images (ignore errors if already removed).
    for tag in (ITEST_SHELL_IMAGE, ITEST_L0_IMAGE):
        subprocess.run(["podman", "rmi", "-f", tag], capture_output=True, timeout=30)
