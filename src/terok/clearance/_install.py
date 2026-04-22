# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install ``terok-clearance-notifier.service`` into the user's systemd tree.

Mirrors ``terok_dbus._install.install_service`` for the notifier half
of the clearance pair.  The hub unit is installed by terok-dbus's own
installer; the notifier unit lives in terok because terok owns the
entry point (``terok-clearance-notifier``) and the systemd-resource
template.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # Python 3.9+
    from importlib.resources import files as _resource_files
except ImportError:  # pragma: no cover — older than the supported floor
    from importlib_resources import files as _resource_files  # type: ignore

UNIT_NAME = "terok-clearance-notifier.service"
_TEMPLATE_BIN_TOKEN = "{{BIN}}"


def default_unit_path() -> Path:
    """Return the canonical user-systemd path for the notifier unit."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else Path.home() / ".config"
    return config_home / "systemd" / "user" / UNIT_NAME


def render_unit(bin_path: str | Path | list[str]) -> str:
    """Return the systemd unit text with ``{{BIN}}`` replaced.

    ``bin_path`` may be a string/path (single executable) or a list
    (e.g. ``[sys.executable, "-m", "terok"]``) for systems where the
    entry-point shim isn't on PATH.  A list is shell-joined with
    spaces; the systemd unit line is fine with that.
    """
    tpl = _resource_files("terok.resources.systemd").joinpath(UNIT_NAME).read_text("utf-8")
    replacement = " ".join(bin_path) if isinstance(bin_path, list) else str(bin_path)
    return tpl.replace(_TEMPLATE_BIN_TOKEN, replacement)


def install_service(bin_path: str | Path | list[str]) -> Path:
    """Render the unit template, write it to ``~/.config/systemd/user/``, return the path.

    Idempotent — overwrites the existing unit so an in-place
    ``terok setup`` run refreshes the ``ExecStart`` path if the
    entry-point moved (common after a pipx reinstall).  The operator
    runs ``systemctl --user daemon-reload`` and ``enable --now``
    themselves (or the setup orchestrator calls them after us).
    """
    dest = default_unit_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_unit(bin_path), encoding="utf-8")
    return dest
