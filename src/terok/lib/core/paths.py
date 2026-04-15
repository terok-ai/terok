# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Platform-aware path resolution for config, state, and runtime directories."""

import getpass
import os
import warnings
from pathlib import Path

try:
    from platformdirs import (
        user_config_dir as _user_config_dir,
        user_data_dir as _user_data_dir,
    )
except ImportError:  # optional dependency
    _user_config_dir = _user_data_dir = None  # type: ignore[assignment]


APP_NAME = "terok"
_VAULT_SUBDIR = "vault"


def _is_root() -> bool:
    """Return True if the current process is running as root."""
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return getpass.getuser() == "root"


def config_root() -> Path:
    """
    Base directory for configuration (project.yml, projects/, etc.).

    Priority:
      1. TEROK_CONFIG_DIR
      2. if root   → /etc/terok
         else      → ~/.config/terok
    """
    env = os.getenv("TEROK_CONFIG_DIR")
    if env:
        return Path(env).expanduser()

    if _is_root():
        return Path("/etc") / APP_NAME

    if _user_config_dir is not None:
        return Path(_user_config_dir(APP_NAME))
    return Path.home() / ".config" / APP_NAME


def state_root() -> Path:
    """
    Writable state (tasks, pods, caches).

    Priority:
      1. TEROK_STATE_DIR
      2. if root   → /var/lib/terok
         else      → ${XDG_DATA_HOME:-~/.local/share}/terok
    """
    env = os.getenv("TEROK_STATE_DIR")
    if env:
        return Path(env).expanduser()

    if _is_root():
        return Path("/var/lib") / APP_NAME

    if _user_data_dir is not None:
        return Path(_user_data_dir(APP_NAME))

    # Fallback without platformdirs: honor XDG_DATA_HOME if set
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def runtime_root() -> Path:
    """
    Transient runtime bits.

    Priority:
      1. TEROK_RUNTIME_DIR
      2. if root   → /run/terok
         else      → ~/.cache/terok
    """
    env = os.getenv("TEROK_RUNTIME_DIR")
    if env:
        return Path(env).expanduser()

    if _is_root():
        return Path("/run") / APP_NAME

    return Path.home() / ".cache" / APP_NAME


def vault_root() -> Path:
    """Shared vault directory used by all terok ecosystem packages.

    Houses token broker DB, SSH signer keys, and agent config mounts.
    Lives under the ``terok/`` namespace so a single ``rm -rf`` or backup
    captures everything.

    Priority: ``TEROK_VAULT_DIR`` → ``TEROK_CREDENTIALS_DIR`` (deprecated
    fallback) → ``/var/lib/terok/vault`` (root) → XDG data dir.
    """
    env = os.getenv("TEROK_VAULT_DIR")
    if env:
        return Path(env).expanduser()
    legacy_env = os.getenv("TEROK_CREDENTIALS_DIR")
    if legacy_env:
        warnings.warn(
            "TEROK_CREDENTIALS_DIR is deprecated; use TEROK_VAULT_DIR instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(legacy_env).expanduser()
    if _is_root():
        return Path("/var/lib") / APP_NAME / _VAULT_SUBDIR
    if _user_data_dir is not None:
        return Path(_user_data_dir(APP_NAME)) / _VAULT_SUBDIR
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME / _VAULT_SUBDIR
    return Path.home() / ".local" / "share" / APP_NAME / _VAULT_SUBDIR
