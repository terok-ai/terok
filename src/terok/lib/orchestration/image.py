# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Dockerfile generation, image building, and build-context hashing.

[`ProjectImage`][terok.lib.orchestration.image.ProjectImage] is the
entry point: instantiate from a project name or
[`ProjectConfig`][terok.lib.core.project_model.ProjectConfig] and call
[`build`][terok.lib.orchestration.image.ProjectImage.build] /
[`generate_dockerfiles`][terok.lib.orchestration.image.ProjectImage.generate_dockerfiles].
L0 (base dev) and L1 (agent CLI) image builds are delegated to
``terok_executor.container.build``; this module owns L2 (project
customisation) rendering, hashing, and manifest book-keeping.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from functools import cached_property, lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import jinja2
from terok_util import ensure_dir

from terok.lib.integrations.executor import (
    AgentRoster,
    BuildError,
    ImageBuilder,
    build_project_image,
)

from ..core.config import build_dir
from ..core.images import project_cli_image, project_dev_image
from ..core.project_model import ProjectConfig
from ..core.projects import load_project

_MANIFEST_SCHEMA = 1
_logger = logging.getLogger(__name__)


# ── ProjectImage: per-project build orchestrator ───────────


@dataclass(frozen=True)
class ProjectImage:
    """Per-project build orchestrator over the L0/L1/L2 image stack.

    Holds a [`ProjectConfig`][terok.lib.core.project_model.ProjectConfig]
    and exposes everything that operates on it: Dockerfile rendering,
    per-layer + combined content hashes, manifest read/write, and the
    L0/L1/L2 build pipeline.  L0+L1 are delegated to
    ``terok_executor.container.build``; L2 is rendered + built here.

    The build context hash and rendered Dockerfile dict are cached on
    the instance — repeated reads (manifest staleness checks, hash
    comparisons) don't re-render templates or re-hash bundled resources.
    """

    project: ProjectConfig
    """Resolved project configuration."""

    @classmethod
    def load(cls, project_name: str) -> ProjectImage:
        """Construct from *project_name* via [`load_project`][terok.lib.core.projects.load_project]."""
        return cls(load_project(project_name))

    # ── Family + rendered Dockerfiles ────────────────

    @cached_property
    def family(self) -> str:
        """Resolved package family (``"deb"``/``"rpm"``) for the base image."""
        return ImageBuilder.detect_family(self.project.base_image, self.project.family)

    @cached_property
    def rendered(self) -> dict[str, str]:
        """Rendered Dockerfile contents keyed by filename."""
        return {
            "L0.Dockerfile": ImageBuilder(self.project.base_image, family=self.family).render_l0(),
            "L1.cli.Dockerfile": ImageBuilder.render_l1(
                ImageBuilder(self.project.base_image).l0_tag, family=self.family
            ),
            "L2.Dockerfile": self._render_l2(),
        }

    def render_all_dockerfiles(self) -> dict[str, str]:
        """Public alias for [`rendered`][terok.lib.orchestration.image.ProjectImage.rendered]."""
        return self.rendered

    # ── Per-layer + combined content hashes ─────────

    @property
    def l0_content_hash(self) -> str:
        """Content hash for the L0 (base dev) layer."""
        return _sha256(f"base_image={self.project.base_image}", self.rendered["L0.Dockerfile"])

    @property
    def l1_content_hash(self) -> str:
        """Content hash for the L1 (agent CLI) layer."""
        return _sha256(self.rendered["L1.cli.Dockerfile"], _scripts_hash(), _tmux_config_hash())

    @property
    def l2_content_hash(self) -> str:
        """Content hash for the L2 (project customisation) layer."""
        return _sha256(self.rendered["L2.Dockerfile"])

    @cached_property
    def context_hash(self) -> str:
        """Combined L0+L1+L2 build-context hash (stamped on L2 images)."""
        return _sha256(self.l0_content_hash, self.l1_content_hash, self.l2_content_hash)

    # ── Build context staging ────────────────────────

    @property
    def stage_dir(self) -> Path:
        """Build-context directory for this project (``build_dir()/<id>``)."""
        return build_dir() / self.project.name

    def generate_dockerfiles(self) -> None:
        """Render and write Dockerfiles and auxiliary scripts to the stage dir.

        Threads the resolved family through so the staging step doesn't
        re-detect from ``base_image``.
        """
        out_dir = self.stage_dir
        ensure_dir(out_dir)

        for name, content in self.rendered.items():
            (out_dir / name).write_text(content)

        # Stage auxiliary resources from terok-executor into build context.
        for staging_step, label in (
            (lambda d: ImageBuilder.stage_scripts(d / "scripts"), "build scripts"),
            (lambda d: ImageBuilder.stage_toad_agents(d / "toad-agents"), "toad agent definitions"),
            (lambda d: ImageBuilder.stage_tmux_config(d / "tmux"), "tmux config"),
        ):
            try:
                staging_step(out_dir)
            except OSError as e:
                print(f"Warning: could not stage {label}: {e}")

        print(f"Generated Dockerfiles in {out_dir}")

    # ── Build manifest ───────────────────────────────

    @property
    def manifest_path(self) -> Path:
        """Path to the on-disk build manifest for this project."""
        return build_dir() / self.project.name / "build_manifest.json"

    def read_manifest(self) -> dict[str, Any] | None:
        """Load the build manifest, or ``None`` if absent/corrupt."""
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and data.get("schema") == _MANIFEST_SCHEMA else None

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        """Atomically write the build manifest."""
        path = self.manifest_path
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    # ── Build pipeline ───────────────────────────────

    def build(
        self,
        *,
        include_dev: bool = False,
        refresh_agents: bool = False,
        full_rebuild: bool = False,
        agents: str | None = None,
    ) -> None:
        """Build the L0/L1/L2 image stack for this project.

        L0+L1 builds are delegated to ``ImageBuilder.build_base()``.
        L2 (project customisation) is built locally on top of L1; with
        ``include_dev=True`` an additional L2-dev image is built on L0.

        Args:
            include_dev: Also build the L2 dev image (tagged ``<project>:l2-dev``).
            refresh_agents: Rebuild L1 with fresh agents (cache bust); L0
                is re-run fully cached.
            full_rebuild: Rebuild every layer with ``--no-cache --pull=always``.
            agents: One-shot override for the agent selection.  ``None``
                uses ``project.agents`` (which inherits from the global
                ``image.agents`` config).
        """
        rebuilt_base = refresh_agents or full_rebuild
        agents_arg = AgentRoster.parse_selection(
            agents if agents is not None else self.project.agents
        )

        # Delegate L0+L1 to terok-executor (uses its own temp dir for build context).
        try:
            base_images = ImageBuilder(
                self.project.base_image, family=self.project.family
            ).build_base(
                agents=agents_arg,
                rebuild=refresh_agents,
                full_rebuild=full_rebuild,
            )
        except BuildError as e:
            raise SystemExit(str(e)) from e

        l1_cli_image = base_images.l1
        l0_image = base_images.l0
        l2_cli_image = project_cli_image(self.project.name)
        l2_dev_image = project_dev_image(self.project.name)

        # Generate L2 build context (Dockerfile + staged resources).
        self.generate_dockerfiles()
        l2_path = self.stage_dir / "L2.Dockerfile"

        # Resolve manifest L0/L1 hashes: use current hashes if rebuilt,
        # carry forward from previous manifest if skipped.
        if rebuilt_base:
            manifest_l0_hash, manifest_l1_hash = self.l0_content_hash, self.l1_content_hash
        else:
            prev = self.read_manifest()
            manifest_l0_hash = prev["l0"]["content_hash"] if prev else self.l0_content_hash
            manifest_l1_hash = prev["l1"]["content_hash"] if prev else self.l1_content_hash

        def _build_l2(base_arg: str, target: str) -> None:
            """Build one L2 image variant — thin delegate to the executor's factory."""
            try:
                build_project_image(
                    dockerfile=l2_path,
                    context_dir=self.stage_dir,
                    target_tag=target,
                    build_args={"BASE_IMAGE": base_arg},
                    labels={"terok.build_context_hash": self.context_hash},
                    no_cache=full_rebuild,
                )
            except BuildError as exc:
                raise SystemExit(str(exc)) from exc

        # Always build L2 CLI image (project layer on top of L1).
        _build_l2(l1_cli_image, l2_cli_image)

        # Optionally build L2 dev image (project layer on top of L0).
        if include_dev:
            _build_l2(l0_image, l2_dev_image)

        # Write build manifest so staleness detection knows what each
        # layer was actually built from.
        self._write_manifest(
            {
                "schema": _MANIFEST_SCHEMA,
                "base_image": self.project.base_image,
                "l0": {"tag": l0_image, "content_hash": manifest_l0_hash},
                "l1": {"tag": l1_cli_image, "content_hash": manifest_l1_hash},
                "l2_cli": {"tag": l2_cli_image, "content_hash": self.l2_content_hash},
                "combined_hash": self.context_hash,
            }
        )

    # ── L2 (project) Dockerfile rendering ──────────

    def _render_l2(self) -> str:
        """Render the L2 (project customisation) Dockerfile.

        L2 contains the user image snippet wrapped in USER root/dev.
        Runtime env vars (CODE_REPO, GIT_BRANCH) are set by
        ``environment.py`` at container launch time.
        """
        variables = {
            "CODE_REPO_DEFAULT": (
                "file:///git-gate/gate.git"
                if self.project.security_class == "gatekeeping"
                else (self.project.upstream_url or "")
            ),
            "DEFAULT_BRANCH": self.project.default_branch or "",
            "USER_SNIPPET": self._resolve_user_snippet(),
        }
        template = (
            resources.files("terok") / "resources" / "templates" / "l2.project.Dockerfile.template"
        )
        with resources.as_file(template) as template_path:
            # ``StrictUndefined`` upgrades silent ``{{TYPO}}`` to a hard
            # error; ``autoescape=False`` because Dockerfile syntax is not
            # HTML and any escaping would corrupt ``RUN`` commands.
            env = jinja2.Environment(  # nosec B701 — see comment above  # noqa: S701
                loader=jinja2.FileSystemLoader(str(template_path.parent)),
                keep_trailing_newline=True,
                undefined=jinja2.StrictUndefined,
                autoescape=False,
            )
            return env.get_template(template_path.name).render(**variables)

    def _resolve_user_snippet(self) -> str:
        """Resolve the user snippet from project config (file and/or inline).

        When both ``image.user_snippet_file`` and
        ``image.user_snippet_inline`` are set, the file is included first
        and the inline block is appended.

        Raises [`SystemExit`][SystemExit] if
        ``image.user_snippet_file`` is configured but the file does not
        exist or cannot be read.
        """
        parts: list[str] = []
        if self.project.snippet_file:
            us_path = Path(self.project.snippet_file).expanduser()
            if not us_path.is_absolute():
                us_path = self.project.root / us_path
            if not us_path.is_file():
                raise SystemExit(
                    f"image.user_snippet_file not found: {us_path}\n"
                    f"  (configured in project '{self.project.name}')"
                )
            try:
                parts.append(us_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                raise SystemExit(f"Failed to read image.user_snippet_file {us_path}: {exc}")
        if self.project.snippet_inline and self.project.snippet_inline.strip():
            parts.append(self.project.snippet_inline)
        return "\n".join(parts)


# ── Image existence ───────────────────────────────────────


def image_exists(image: str) -> bool:
    """Return True when *image* is present in the local container store."""
    return _image_exists(image)


def _image_exists(image: str) -> bool:
    """Same check as [`image_exists`][terok.lib.orchestration.image.image_exists], kept as a separate symbol for tests.

    The public function resolves this name on every call, so
    ``patch("terok.lib.orchestration.image._image_exists", fake)`` reaches
    every caller — an ``image_exists = _image_exists`` alias would not.
    """
    # Image existence is runtime-agnostic — podman's image store is shared.
    from terok.lib.integrations.sandbox import PodmanRuntime

    return PodmanRuntime().image(image).exists()


# ── Hashing helpers (shared module-level) ─────────────────


def _hash_traversable_tree(root: Traversable) -> str:
    """Compute a SHA-256 digest over all files in a Traversable tree."""
    hasher = hashlib.sha256()

    def _walk(node: Traversable, prefix: str) -> None:
        """Walk a Traversable tree and feed file contents into the hasher."""
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            rel = f"{prefix}{child.name}"
            if child.is_dir():
                _walk(child, f"{rel}/")
            else:
                hasher.update(rel.encode("utf-8"))
                hasher.update(b"\0")
                hasher.update(child.read_bytes())
                hasher.update(b"\0")

    _walk(root, "")
    return hasher.hexdigest()


@lru_cache(maxsize=1)
def _scripts_hash() -> str:
    """Return a cached SHA-256 hash of the bundled helper scripts."""
    scripts_root = resources.files("terok_executor") / "resources" / "scripts"
    return _hash_traversable_tree(scripts_root)


@lru_cache(maxsize=1)
def _tmux_config_hash() -> str:
    """Return a cached SHA-256 hash of the bundled tmux configuration."""
    tmux_root = resources.files("terok_executor") / "resources" / "tmux"
    return _hash_traversable_tree(tmux_root)


def _sha256(*parts: str) -> str:
    """Compute SHA-256 from a sequence of string parts, null-separated."""
    hasher = hashlib.sha256()
    for i, part in enumerate(parts):
        if i:
            hasher.update(b"\0")
        hasher.update(part.encode("utf-8"))
    return hasher.hexdigest()


# ── Thin module-level shims (back-compat with pre-W5.C.2 callers) ────


def render_all_dockerfiles(project: ProjectConfig, *, family: str | None = None) -> dict[str, str]:
    """Shim around [`ProjectImage.rendered`][terok.lib.orchestration.image.ProjectImage.rendered]."""
    # Family override is honoured by ProjectImage when it's pre-set on
    # the project; the caller's *family* kwarg used to bypass detection,
    # which is now folded into ProjectImage.family.
    image = ProjectImage(project)
    if family is not None:
        # Bypass detection by pre-populating the cached_property slot.
        object.__setattr__(image, "family", family)
    return image.rendered


def l0_content_hash(base_image: str, rendered: dict[str, str]) -> str:
    """Shim around [`ProjectImage.l0_content_hash`][terok.lib.orchestration.image.ProjectImage.l0_content_hash]."""
    return _sha256(f"base_image={base_image}", rendered["L0.Dockerfile"])


def l1_content_hash(rendered: dict[str, str]) -> str:
    """Shim around [`ProjectImage.l1_content_hash`][terok.lib.orchestration.image.ProjectImage.l1_content_hash]."""
    return _sha256(rendered["L1.cli.Dockerfile"], _scripts_hash(), _tmux_config_hash())


def l2_content_hash(rendered: dict[str, str]) -> str:
    """Shim around [`ProjectImage.l2_content_hash`][terok.lib.orchestration.image.ProjectImage.l2_content_hash]."""
    return _sha256(rendered["L2.Dockerfile"])


def build_context_hash_from_rendered(project: ProjectConfig, rendered: dict[str, str]) -> str:
    """Shim composing the three layer hashes for back-compat callers."""
    return _sha256(
        l0_content_hash(project.base_image, rendered),
        l1_content_hash(rendered),
        l2_content_hash(rendered),
    )


def build_context_hash(project_name: str) -> str:
    """Shim around [`ProjectImage.context_hash`][terok.lib.orchestration.image.ProjectImage.context_hash]."""
    return ProjectImage.load(project_name).context_hash


def _write_build_manifest(project_name: str, manifest: dict[str, Any]) -> None:
    """Shim around [`ProjectImage._write_manifest`][terok.lib.orchestration.image.ProjectImage._write_manifest]."""
    ProjectImage.load(project_name)._write_manifest(manifest)


def _manifest_path(project_name: str) -> Path:
    """Shim around [`ProjectImage.manifest_path`][terok.lib.orchestration.image.ProjectImage.manifest_path]."""
    return ProjectImage.load(project_name).manifest_path


def read_build_manifest(project_name: str) -> dict[str, Any] | None:
    """Shim around [`ProjectImage.read_manifest`][terok.lib.orchestration.image.ProjectImage.read_manifest]."""
    return ProjectImage.load(project_name).read_manifest()


def generate_dockerfiles(project_name: str, *, family: str | None = None) -> None:
    """Shim around [`ProjectImage.generate_dockerfiles`][terok.lib.orchestration.image.ProjectImage.generate_dockerfiles]."""
    image = ProjectImage.load(project_name)
    if family is not None:
        object.__setattr__(image, "family", family)
    image.generate_dockerfiles()


def build_images(
    project_name: str,
    include_dev: bool = False,
    refresh_agents: bool = False,
    full_rebuild: bool = False,
    agents: str | None = None,
) -> None:
    """Shim around [`ProjectImage.build`][terok.lib.orchestration.image.ProjectImage.build]."""
    ProjectImage.load(project_name).build(
        include_dev=include_dev,
        refresh_agents=refresh_agents,
        full_rebuild=full_rebuild,
        agents=agents,
    )
