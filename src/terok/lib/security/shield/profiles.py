# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Profile loading and composition for shield firewall rules."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from .config import shield_profiles_dir

_BUNDLED_PACKAGE = "terok.resources.shield.profiles"


def _profile_path(name: str) -> Path | None:
    """Find a profile by name.  User dir takes precedence over bundled."""
    # User override
    user_path = shield_profiles_dir() / f"{name}.nft"
    if user_path.is_file():
        return user_path

    # Bundled resource
    try:
        ref = importlib.resources.files(_BUNDLED_PACKAGE).joinpath(f"{name}.nft")
        if ref.is_file():  # type: ignore[union-attr]
            return Path(str(ref))
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        pass

    return None


def load_profile(name: str) -> str:
    """Load a single profile's nft fragment by name.

    Raises FileNotFoundError if the profile is not found.
    """
    path = _profile_path(name)
    if path is None:
        raise FileNotFoundError(f"Shield profile not found: {name}")
    return path.read_text()


def profile_path(name: str) -> Path:
    """Return the resolved path of a profile.

    Raises FileNotFoundError if the profile is not found.
    """
    path = _profile_path(name)
    if path is None:
        raise FileNotFoundError(f"Shield profile not found: {name}")
    return path


def compose_profiles(names: list[str]) -> str:
    """Load and concatenate multiple profiles into a single nft fragment."""
    fragments: list[str] = []
    for name in names:
        fragments.append(load_profile(name))
    return "\n".join(fragments)


def list_profiles() -> list[str]:
    """List all available profile names (user + bundled, deduplicated)."""
    names: set[str] = set()

    # User profiles
    user_dir = shield_profiles_dir()
    if user_dir.is_dir():
        for f in user_dir.glob("*.nft"):
            names.add(f.stem)

    # Bundled profiles
    try:
        pkg = importlib.resources.files(_BUNDLED_PACKAGE)
        for item in pkg.iterdir():  # type: ignore[union-attr]
            name = str(item.name) if hasattr(item, "name") else str(item).rsplit("/", 1)[-1]
            if name.endswith(".nft"):
                names.add(name[:-4])
    except (TypeError, FileNotFoundError, ModuleNotFoundError):
        pass

    return sorted(names)
