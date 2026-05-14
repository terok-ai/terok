# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Context-managed access to the shared vault `CredentialDB`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.integrations.sandbox import CredentialDB


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
    from terok.lib.integrations.sandbox import NoPassphraseError, WrongPassphraseError

    # Catch exceptions raised on entry only — wrapping the entire
    # ``with vault_db(): yield db`` in a try/except would also swallow
    # any locked-vault error that escaped the consumer's block and
    # cause a second yield, breaking the contextmanager contract.
    # ``ExitStack`` lets us scope the catch to ``__enter__`` and still
    # guarantee teardown of the inner context.
    with ExitStack() as stack:
        try:
            db = stack.enter_context(vault_db(prompt_on_tty=prompt_on_tty))
        except (NoPassphraseError, WrongPassphraseError):
            yield None
            return
        yield db


__all__ = ["maybe_vault_db", "vault_db"]
