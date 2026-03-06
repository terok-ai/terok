# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import unittest
import unittest.mock


class ProjectWizardDispatchTests(unittest.TestCase):
    """Tests for the project-wizard CLI command dispatch."""

    @unittest.mock.patch("terok.cli.commands.project.run_wizard")
    def test_project_wizard_dispatch(self, mock_wizard: unittest.mock.Mock) -> None:
        from terok.cli.commands.setup import cmd_project_init
        from terok.cli.main import main

        with unittest.mock.patch("sys.argv", ["terok", "project-wizard"]):
            main()

        mock_wizard.assert_called_once()
        _, kwargs = mock_wizard.call_args
        self.assertIs(kwargs.get("init_fn"), cmd_project_init)


if __name__ == "__main__":
    unittest.main()
