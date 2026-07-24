# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit-test fixtures.

Auto-mocks sandbox, shield, and credential proxy helpers so existing tests
do not require a real OCI hook, nftables, podman, proxy daemon, or root
privileges.

The ``_isolate_user_paths`` fixture redirects ``HOME`` and the ``XDG_*``
chain to a per-test temp dir so no test ever resolves to a real
``~/.config/terok`` / XDG state path.  The ``_isolate_port_registry``
fixture does the analogous job for the file-based port registry.
"""

from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Terok-specific env vars that override path resolution.  The autouse
# isolation fixture unsets each so resolution falls back through the
# tmp-rooted ``HOME`` / ``XDG_*`` chain — never to the operator's real
# state.  Kept in one place so a new ``TEROK_*_DIR`` knob added in
# terok, sandbox, or executor only needs one edit here.
_TEROK_PATH_OVERRIDE_ENV_VARS = (
    "TEROK_CONFIG_DIR",
    "TEROK_STATE_DIR",
    "TEROK_VAULT_DIR",
    "TEROK_RUNTIME_DIR",
    "TEROK_ROOT",
    "TEROK_SANDBOX_LIVE_DIR",
    "TEROK_SANDBOX_STATE_DIR",
    "TEROK_SANDBOX_RUNTIME_DIR",
    "TEROK_EXECUTOR_STATE_DIR",
    "TEROK_PORT_REGISTRY_DIR",
)


@pytest.fixture(autouse=True)
def _isolate_user_paths(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirect ``HOME`` and every ``XDG_*`` / ``TEROK_*_DIR`` knob to a fresh tmp dir.

    Without this, tests that exercise default-config code paths (e.g.
    ``SandboxConfig()`` with no overrides, ``handle_*(cfg=None)``) fall
    through to the operator's real ``~/.config/terok/config.yml`` and
    XDG state dirs — silently passing on a clean machine and mutating
    those files on a populated one.  Integration tests are already
    isolated via the ``terok_env`` fixture in
    ``tests/integration/conftest.py``; this is the unit-test equivalent.

    Uses ``tmp_path_factory`` rather than ``tmp_path`` so the fake home
    lives outside the per-test ``tmp_path`` — tests that iterate their
    own ``tmp_path`` looking for fixtures would otherwise see a stray
    ``fake-home`` entry.  The per-test ``monkeypatch`` undoes the env
    overrides at teardown, so tests that need different env state can
    layer their own ``setenv`` / ``delenv`` calls on top.
    """
    fake_home = tmp_path_factory.mktemp("fake-home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(fake_home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(fake_home / ".cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(fake_home / "run"))
    for var in _TEROK_PATH_OVERRIDE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # CI containers whose ``uid_map`` maps ``geteuid()`` → 0 trip the
    # post-userns ``_is_root()`` → paths route to ``/var/lib/terok``.
    # Tests run as non-root by definition.
    monkeypatch.setattr("terok_util.paths._is_root", lambda: False)
    # CLI ``--config`` / ``--raw`` mutate ``os.environ`` directly,
    # bypassing monkeypatch — clear per-test to avoid leakage.
    monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
    # Never let tests act on the operator's real tmux: with these set
    # (e.g. pytest run inside ``terok tui --tmux``), tmux_session helpers
    # would stamp windows and flash messages on the live session.
    for var in ("TMUX", "TMUX_PANE", "TEROK_TMUX"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every unit test from a throwaway working directory.

    A test that hands a bare ``Mock()`` where production code expects a
    ``SandboxConfig`` can have ``str(mock.some_path)`` used as a *relative*
    filename, so the resulting write lands in the process CWD.  When that
    CWD is the repo checkout, a file named like ``<Mock name='...' id=…>``
    is created at the repo root, swept into ``git add -A``, and — as
    happened once — committed and merged.  Anchoring every test's CWD in a
    fresh temp dir sends any such stray *relative* write to disposable
    scratch space instead of the working tree; the dir is reclaimed with
    the rest of ``tmp_path_factory``'s tree.  Absolute paths are already
    covered by the ``HOME`` / ``XDG_*`` isolation above.
    """
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture(autouse=True)
def _reset_config_caches() -> Iterator[None]:
    """Clear config caches between tests to prevent cross-test pollution."""
    from terok_util import paths as _util_paths

    import terok.lib.core.config as _config

    _util_paths._reset_config_caches_for_tests()
    _config._validated_config_cache = None
    _config._raw_config_cache = None
    yield
    _util_paths._reset_config_caches_for_tests()
    _config._validated_config_cache = None
    _config._raw_config_cache = None


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect port registry to tmp dirs so tests never touch the real FS.

    Patches the shared claims directory and suppresses per-user backup
    writes so that tests never touch ``/tmp/terok-ports/`` or the real
    state directory.
    """
    import terok_sandbox.port_registry as _reg

    registry = tmp_path / "terok-ports"
    registry.mkdir()
    monkeypatch.setattr(_reg._default, "registry_dir", registry)
    monkeypatch.setattr(_reg, "_save_ports", lambda _sd, _p: None)
    monkeypatch.setenv("TEROK_PORT_REGISTRY_DIR", str(registry))
    _reg.reset_cache()


@pytest.fixture(autouse=True)
def _mock_infrastructure() -> Iterator[None]:
    """Replace Sandbox.run, shield down, and vault with no-ops."""
    with (
        patch(
            "terok.lib.orchestration.task_runners.container._agent_runner",
        ),
        patch(
            "terok.lib.integrations.sandbox.ShieldManager.down",
        ),
        patch(
            "terok.lib.core.config.get_vault_bypass",
            return_value=True,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _no_shield_self_confinement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise shield's irreversible Landlock self-floor for in-process tests.

    Dispatching ``shield watch`` / ``shield simple-clearance`` enters shield's
    reader handlers, which call ``confine_to_state`` — Landlock ``restrict_self``,
    process-wide and permanent.  Left live it confines the pytest worker itself,
    so every later test errors trying to open its temp/coverage files.  The real
    floor is proved on the live kernel in terok-shield's own subprocess test.
    """
    monkeypatch.setattr("terok_shield._confine.confine_to_state", lambda _state_dir: None)


@pytest.fixture(autouse=True)
def _stub_credential_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Stub ``SandboxConfig.open_credential_db`` so tests never hit the resolution chain.

    After at-rest encryption (terok-sandbox#268), opening the
    credential DB requires a passphrase that resolves through the
    chain (session-file → keyring → config → prompt).  Unit-test
    runners have none of those, so every ``vault_db()`` /
    ``maybe_vault_db()`` consumer would raise ``NoPassphraseError``
    in CI.  Provision a real per-session ``CredentialDB`` backed by
    a SQLCipher file with a known test passphrase — tests that mock
    deeper (``cfg.open_credential_db.return_value = MagicMock()``)
    override this stub.

    The stub mirrors the real signature — ``open_credential_db(db_path=None,
    *, prompt_on_tty=False)`` — because callers like
    [`list_authenticated_agents`][terok_executor.list_authenticated_agents]
    pass ``db_path`` positionally; a keyword-only stub raises ``TypeError``
    on exactly the vault-backed code paths tests need to exercise.  The
    ``db_path`` override is deliberately ignored: every test reads and
    writes the one per-session vault file.
    """
    from terok_sandbox import CredentialDB

    db_path = tmp_path_factory.mktemp("unit-vault") / "credentials.db"
    test_passphrase = "unit-test-passphrase"  # nosec: B105 — fixture, not a real secret

    def _open(_db_path: Path | None = None, *, prompt_on_tty: bool = False) -> CredentialDB:
        return CredentialDB(db_path, passphrase=test_passphrase)

    with patch(
        "terok_sandbox.config.SandboxConfig.open_credential_db",
        new=lambda self, db_path=None, *, prompt_on_tty=False: _open(
            db_path, prompt_on_tty=prompt_on_tty
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_runtime() -> Iterator[MagicMock]:
    """Install a fresh [`MagicMock`][unittest.mock.MagicMock] as the per-project ``ContainerRuntime``.

    Every unit test gets an isolated mock runtime patched into
    [`terok.lib.core.runtime.resolve_runtime`][terok.lib.core.runtime.resolve_runtime]
    and the project-agnostic ``PodmanRuntime`` constructor that
    image-management call sites use.  Tests that care about specific
    container-level behaviour configure the mock directly
    (``mock_runtime.container.return_value.state = "running"``); tests
    that don't care pay no cost beyond the patch overhead.

    Defaults are set so that common code paths don't trip on
    "a Mock is not iterable" or similar:

    - ``container_states`` / ``container_rw_sizes`` → ``{}``
    - ``images`` / ``force_remove`` → ``[]``
    - ``container(...).wait()`` → ``0`` (benign exit code)
    - ``container(...).login_command(...)`` → a realistic podman argv
    - ``container(...).stream_initial_logs(...)`` → ``True`` (ready)

    Runs *after* ``_mock_infrastructure`` so its ``_agent_runner``
    patch is still in place.
    """
    fake = MagicMock(name="mock_runtime")
    fake.container_states.return_value = {}
    fake.container_rw_sizes.return_value = {}
    fake.images.return_value = []
    fake.force_remove.return_value = []
    container = fake.container.return_value
    container.wait.return_value = 0
    container.login_command.return_value = ["podman", "exec", "-it", "ctr", "bash"]
    container.stream_initial_logs.return_value = True
    # Image-management call sites bypass the resolver and construct
    # ``PodmanRuntime`` directly.  Each ``from terok.lib.integrations.sandbox
    # import PodmanRuntime`` creates a module-local binding that survives a
    # patch on the source module, so we patch every binding the production
    # code uses — keeps the fixture self-contained.
    _podman_runtime_sites = (
        "terok.lib.api.PodmanRuntime",
        "terok.lib.core.images.PodmanRuntime",
        "terok.lib.domain.image_cleanup.PodmanRuntime",
        "terok.lib.domain.panic.PodmanRuntime",
        "terok.lib.domain.project_state.PodmanRuntime",
        "terok.lib.orchestration.image.PodmanRuntime",
        "terok.lib.orchestration.tasks.query.PodmanRuntime",
    )
    with patch("terok.lib.core.runtime.resolve_runtime", return_value=fake):
        with ExitStack() as stack:
            for site in _podman_runtime_sites:
                try:
                    stack.enter_context(patch(site, return_value=fake))
                except (AttributeError, ModuleNotFoundError):
                    # Some sites import lazily inside a function; the
                    # module-level name doesn't exist until the function
                    # runs.  Lazy importers also re-bind to whatever
                    # ``terok.lib.integrations.sandbox.PodmanRuntime`` is,
                    # so patching the source once covers them.
                    pass
            stack.enter_context(
                patch("terok.lib.integrations.sandbox.PodmanRuntime", return_value=fake)
            )
            yield fake
