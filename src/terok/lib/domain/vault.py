# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Context-managed access to the shared vault `CredentialDB`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok_sandbox import CredentialDB


@contextmanager
def vault_db(*, prompt_on_tty: bool = False) -> Iterator[CredentialDB]:
    """Open the shared vault ``CredentialDB`` and close it on exit.

    Routes through ``SandboxConfig.open_credential_db`` so the four-tier
    passphrase resolution chain (session-unlock file → keyring → config
    fallback → optional prompt) runs.  Daemons and background workers
    leave ``prompt_on_tty=False`` so a locked vault fails fast with a
    clear ``NoPassphraseError`` instead of stalling on stdin; CLI
    front-ends pass ``True`` to unlock the interactive last-resort
    prompt.
    """
    from ..core.config import make_sandbox_config

    db = make_sandbox_config().open_credential_db(prompt_on_tty=prompt_on_tty)
    try:
        yield db
    finally:
        db.close()


@contextmanager
def maybe_vault_db(*, prompt_on_tty: bool = False) -> Iterator[CredentialDB | None]:
    """Open the vault DB, yielding ``None`` if the vault is locked.

    Wraps ``vault_db`` for read-only callers that don't need to
    distinguish "no entries" from "vault locked" at their level —
    e.g. project-state tiles that render across the full host.  The
    cross-package exception imports stay confined to this module so
    ``import-linter``'s "terok_sandbox access restricted to designated
    modules" contract holds (only ``vault.py`` reaches into
    ``terok_sandbox``).
    """
    from terok_sandbox.credentials.db import NoPassphraseError, WrongPassphraseError

    try:
        with vault_db(prompt_on_tty=prompt_on_tty) as db:
            yield db
    except (NoPassphraseError, WrongPassphraseError):
        yield None


__all__ = ["maybe_vault_db", "vault_db"]
