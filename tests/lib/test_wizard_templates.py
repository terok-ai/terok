# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import unittest
from importlib import resources
from importlib.resources.abc import Traversable

from terok.lib.util.template_utils import render_template
from terok.lib.wizards.new_project import TEMPLATES as WIZARD_TEMPLATES

TEMPLATE_DIR: Traversable = resources.files("terok") / "resources" / "templates" / "projects"

EXPECTED_TEMPLATES: list[str] = [filename for _label, filename in WIZARD_TEMPLATES]

REQUIRED_PLACEHOLDERS: list[str] = [
    "{{PROJECT_ID}}",
    "{{UPSTREAM_URL}}",
    "{{DEFAULT_BRANCH}}",
    "{{USER_SNIPPET}}",
]


class WizardTemplatesTests(unittest.TestCase):
    """Tests for project wizard YAML templates."""

    def test_all_template_files_exist(self) -> None:
        for name in EXPECTED_TEMPLATES:
            path = TEMPLATE_DIR / name
            self.assertTrue(path.is_file(), f"Template missing: {name}")

    def test_templates_contain_required_placeholders(self) -> None:
        for name in EXPECTED_TEMPLATES:
            path = TEMPLATE_DIR / name
            content = path.read_text(encoding="utf-8")
            for placeholder in REQUIRED_PLACEHOLDERS:
                self.assertIn(
                    placeholder,
                    content,
                    f"{name} missing placeholder {placeholder}",
                )

    def test_online_templates_have_online_security_class(self) -> None:
        for name in ["online-ubuntu.yml", "online-nvidia.yml"]:
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn('security_class: "online"', content)

    def test_gatekeeping_templates_have_gatekeeping_security_class(self) -> None:
        for name in ["gatekeeping-ubuntu.yml", "gatekeeping-nvidia.yml"]:
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn('security_class: "gatekeeping"', content)

    def test_nvidia_templates_have_nvidia_base_image(self) -> None:
        for name in ["online-nvidia.yml", "gatekeeping-nvidia.yml"]:
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("nvcr.io/nvidia/", content)

    def test_ubuntu_templates_have_ubuntu_base_image(self) -> None:
        for name in ["online-ubuntu.yml", "gatekeeping-ubuntu.yml"]:
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("ubuntu:24.04", content)

    def test_nvidia_templates_enable_gpus(self) -> None:
        for name in ["online-nvidia.yml", "gatekeeping-nvidia.yml"]:
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("gpus: all", content)

    def test_gatekeeping_templates_include_gatekeeping_section(self) -> None:
        for name in ["gatekeeping-ubuntu.yml", "gatekeeping-nvidia.yml"]:
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            self.assertIn("gatekeeping:", content)
            self.assertIn("expose_external_remote:", content)

    def test_render_template_replaces_all_placeholders(self) -> None:
        traversable = TEMPLATE_DIR / "online-ubuntu.yml"
        variables = {
            "PROJECT_ID": "my-project",
            "UPSTREAM_URL": "https://github.com/user/repo.git",
            "DEFAULT_BRANCH": "main",
            "USER_SNIPPET": "RUN apt-get update",
        }
        with resources.as_file(traversable) as path:
            rendered = render_template(path, variables)
        self.assertIn('id: "my-project"', rendered)
        self.assertIn('upstream_url: "https://github.com/user/repo.git"', rendered)
        self.assertIn('default_branch: "main"', rendered)
        self.assertIn("RUN apt-get update", rendered)
        for placeholder in REQUIRED_PLACEHOLDERS:
            self.assertNotIn(placeholder, rendered)


if __name__ == "__main__":
    unittest.main()
