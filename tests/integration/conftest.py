# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and skip helpers for integration tests.

This directory currently hosts two integration layers:

- shield integration tests that exercise the real ``terok_shield`` library
- workflow-oriented terok CLI integration tests under ``cli/``, ``projects/``,
  and ``tasks/``

Environment requirements are expressed via pytest markers:

- ``needs_host_features``: real host/filesystem/process behavior only
- ``needs_internet``: outbound network connectivity required
- ``needs_podman``: podman must be available on the host
- ``needs_root``: root-only nftables/shield checks
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import socket
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from terok_util.matrix import binary_on_path, check_capability_contract

from tests.testfs import CONFIG_ROOT_NAME, HOME_DIR_NAME, STATE_ROOT_NAME, XDG_CONFIG_HOME_NAME
from tests.testnet import ALLOWED_TARGET_DOMAIN, ALLOWED_TARGET_HTTP, GATE_PORT, TEST_IP

from .helpers import (
    PODMAN_BASE_IMAGE,
    PODMAN_CONTAINER_PREFIX,
    PODMAN_TEST_IMAGE,
    TerokIntegrationEnv,
    TerokShieldIntegrationEnv,
    start_shielded_container,
)

# One try block per import: bundling them lets a single moved symbol
# silently null the whole set — the import-lazification barrel shrink
# did exactly that, and every capability probe below degraded at once
# (the matrix then reported hooks *and* nft "missing" while both were
# fine).  has_global_hooks lives in the public podman_info submodule.
try:
    from terok_shield import Shield, ShieldConfig, ShieldMode
except ImportError:  # pragma: no cover - optional integration dependency
    Shield = ShieldConfig = ShieldMode = None  # type: ignore[assignment]
try:
    from terok_shield.podman_info import has_global_hooks
except ImportError:  # pragma: no cover - optional integration dependency
    has_global_hooks = None  # type: ignore[assignment]
try:
    from terok_shield.run import find_nft as _shield_find_nft
except ImportError:  # pragma: no cover - optional integration dependency
    _shield_find_nft = None

SHIELD_MISSING_SKIP_REASON = "terok_shield not installed"


def _has(binary: str) -> bool:
    """Return whether *binary* is available on ``PATH``."""
    return shutil.which(binary) is not None


def _find_nft() -> str | None:
    """Return the nft binary path, using terok-shield's sbin-aware lookup when available."""
    return _shield_find_nft() if _shield_find_nft is not None else shutil.which("nft")


def _image_available() -> bool:
    """Return whether the podman integration image is already available locally."""
    result = subprocess.run(
        ["podman", "image", "exists", PODMAN_TEST_IMAGE],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def _target_host_port(url: str) -> tuple[str, int]:
    """Return the host and effective port for a URL used in connectivity checks."""
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError(f"URL missing hostname: {url!r}")
    if parsed.port is not None:
        return parsed.hostname, parsed.port
    if parsed.scheme == "https":
        return parsed.hostname, 443
    return parsed.hostname, 80


# ── Generic skip decorators ───────────────────────────────

git_missing = pytest.mark.skipif(not _has("git"), reason="git not installed")
podman_missing = pytest.mark.skipif(not _has("podman"), reason="podman not installed")
nft_missing = pytest.mark.skipif(not _find_nft(), reason="nft not installed")
ssh_keygen_missing = pytest.mark.skipif(not _has("ssh-keygen"), reason="ssh-keygen not installed")
skip_if_no_root = pytest.mark.skipif(os.geteuid() != 0, reason="root required")


def _hooks_available() -> bool:
    """Return True if global OCI hooks are installed and detectable."""
    if has_global_hooks is None:
        return False
    try:
        return has_global_hooks()
    except Exception:  # pragma: no cover - defensive
        return False


hooks_unavailable = pytest.mark.skipif(
    not _hooks_available(), reason="OCI global hooks not installed"
)


# ── Matrix capability contract ───────────────────────────────────────
# The skip guards above are for dev machines, where a missing binary is
# a host limitation.  Inside the matrix the harness built the image, so
# every capability it declares (TEROK_EXPECT, exported by the matrix engine)
# is a contract: absence fails the whole session up front instead of
# dissolving into skips that read as green.


def _internet_reachable() -> bool:
    host, port = _target_host_port(ALLOWED_TARGET_HTTP)
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


_CAPABILITY_PROBES = {
    "podman": lambda: _has("podman"),
    "nft": lambda: bool(_find_nft()),
    "dnsmasq": lambda: binary_on_path("dnsmasq"),
    "dig": lambda: _has("dig"),
    "getent": lambda: _has("getent"),
    "git": lambda: _has("git"),
    "ssh-keygen": lambda: _has("ssh-keygen"),
    "hooks": _hooks_available,
    "internet": _internet_reachable,
}


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail the whole session when the matrix capability contract is broken."""
    if broken := check_capability_contract(_CAPABILITY_PROBES):
        pytest.exit(broken, returncode=3)


def _reset_layered_config_caches() -> None:
    """Clear terok/sandbox config caches after tests rewrite config files."""
    import terok_sandbox.config as _sandbox_config
    from terok_util import paths as _util_paths

    import terok.lib.core.config as _config

    _util_paths._reset_config_caches_for_tests()
    _config._validated_config_cache = None
    _config._raw_config_cache = None
    for name in (
        "_credentials_section",
        "_gate_server_section",
        "_network_section",
        "_paths_section",
        "_services_section",
        "_shield_section",
        "_ssh_section",
        "_vault_section",
    ):
        cache = getattr(_sandbox_config, name, None)
        if cache is not None and hasattr(cache, "cache_clear"):
            cache.cache_clear()


# ── Mock shield CommandRunner ─────────────────────────────


class MockRunner:
    """Fake CommandRunner that handles known commands for testing."""

    def __init__(self, rootless_mode: str = "pasta") -> None:
        """Create a mock runner with the given rootless network mode."""
        self._rootless_mode = rootless_mode

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        stdin: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Handle known commands and fail fast on unexpected ones."""
        if not cmd:
            raise AssertionError("Unexpected MockRunner command: []")
        if cmd[:2] == ["podman", "info"]:
            return json.dumps(
                {
                    "host": {"rootlessNetworkCmd": self._rootless_mode},
                    "version": {"Version": "5.6.0"},
                }
            )
        if cmd[0] == "dig":
            return f"{TEST_IP}\n"
        if cmd[0] == "nft" or cmd[:2] == ["podman", "inspect"]:
            return ""
        if cmd[:2] == ["podman", "unshare"]:
            return ""
        raise AssertionError(
            f"Unexpected MockRunner command: {cmd!r} (check={check}, stdin={stdin!r}, "
            f"timeout={timeout})"
        )

    def has(self, name: str) -> bool:
        """Return True for nft, False otherwise."""
        return name == "nft"

    def nft(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        """No-op nft command."""
        return ""

    def nft_via_nsenter(
        self,
        container: str,
        *args: str,
        pid: str | None = None,
        stdin: str | None = None,
        check: bool = True,
    ) -> str:
        """No-op nft via nsenter."""
        return ""

    def podman_inspect(self, container: str, fmt: str) -> str:
        """Return fake PID."""
        return "12345"

    def dig_all(self, domain: str, *, timeout: int = 10) -> list[str]:
        """Return the test IP for any domain."""
        return [TEST_IP]


# ── Podman integration preflight ──────────────────────────


@pytest.fixture(scope="session")
def _pull_image() -> None:
    """Build the podman integration test image once per session.

    Extends Alpine with git so shielded-container tests can clone
    without needing outbound access to the Alpine package repos.
    """
    if not _has("podman"):
        pytest.skip("podman not installed")
    if _image_available():
        return
    subprocess.run(
        [
            "podman",
            "build",
            "-t",
            PODMAN_TEST_IMAGE,
            "-f",
            "-",
            ".",
        ],
        # `curl` is for the container-launching story tests
        # (`tests/integration/stories/`); busybox's wget can't speak
        # `--unix-socket`, which the vault-broker socket transport needs.
        input=f"FROM {PODMAN_BASE_IMAGE}\nRUN apk add --no-cache git curl\n",
        check=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="session")
def _verify_connectivity() -> None:
    """Fail fast when the host cannot reach the real egress test target."""
    try:
        socket.getaddrinfo(ALLOWED_TARGET_DOMAIN, None)
    except OSError as exc:
        pytest.fail(
            f"Pre-flight: cannot resolve {ALLOWED_TARGET_DOMAIN} from the host.\n"
            "Fix host DNS resolution before running egress integration tests.\n"
            "Domain-allow tests rely on resolving the allowlisted hostname before applying "
            "the firewall rules.\n"
            f"Error: {exc}"
        )

    host, port = _target_host_port(ALLOWED_TARGET_HTTP)
    try:
        connection = socket.create_connection((host, port), timeout=5)
    except OSError as exc:
        pytest.fail(
            f"Pre-flight: cannot reach {host}:{port} from the host for {ALLOWED_TARGET_HTTP}.\n"
            "Fix host internet connectivity before running egress integration tests.\n"
            "Traffic-based tests would produce false positives when the host network is down.\n"
            f"Error: {exc}"
        )
    else:
        connection.close()


# ── Isolated shield environment ───────────────────────────


@pytest.fixture()
def shield_env(tmp_path: Path) -> TerokShieldIntegrationEnv:
    """Create an isolated per-task shield state directory."""
    task_dir = tmp_path / "tasks" / "test-task"
    state_dir = task_dir / "shield"
    task_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return TerokShieldIntegrationEnv(
        base_dir=tmp_path,
        task_dir=task_dir,
        state_dir=state_dir,
    )


@pytest.fixture()
def shield_config(shield_env: TerokShieldIntegrationEnv) -> ShieldConfig:
    """Standard ShieldConfig for integration tests with per-task state_dir."""
    if ShieldConfig is None or ShieldMode is None:
        pytest.skip(SHIELD_MISSING_SKIP_REASON)
    return ShieldConfig(
        state_dir=shield_env.state_dir,
        mode=ShieldMode.HOOK,
        default_profiles=("dev-standard",),
        loopback_ports=(GATE_PORT,),
        audit_enabled=True,
    )


@pytest.fixture()
def shield(shield_config: ShieldConfig) -> Shield:
    """Shield with a mock runner for no-podman integration tests."""
    if Shield is None:
        pytest.skip(SHIELD_MISSING_SKIP_REASON)
    return Shield(shield_config, runner=MockRunner())


@pytest.fixture()
def real_shield(shield_config: ShieldConfig) -> Shield:
    """Shield with the real subprocess runner for Podman integration tests."""
    if Shield is None:
        pytest.skip(SHIELD_MISSING_SKIP_REASON)
    return Shield(shield_config)


@pytest.fixture()
def mock_runner() -> MockRunner:
    """Return a MockRunner instance for tests that need to customise it."""
    return MockRunner()


_PODMAN_RM_TIMEOUT = 30


def _podman_rm(name: str, *, timeout: int = _PODMAN_RM_TIMEOUT) -> None:
    """Force-remove a container with bounded timeout and error handling."""
    try:
        subprocess.run(
            ["podman", "rm", "-f", name],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - cleanup fallback
        pass


@pytest.fixture()
def shielded_container(_pull_image: None, real_shield: Shield) -> Iterator[str]:
    """Start a disposable podman container with shield hooks applied.

    Skips when global hooks are not installed (required for shield activation).
    """
    if not _hooks_available():
        pytest.skip("OCI global hooks not installed")
    name = f"{PODMAN_CONTAINER_PREFIX}-{uuid.uuid4().hex[:8]}"
    _podman_rm(name)
    try:
        extra_args = real_shield.pre_start(name)
        start_shielded_container(name, extra_args, PODMAN_TEST_IMAGE)
        yield name
    finally:
        _podman_rm(name)


# ── Port registry isolation ───────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect port registry to per-test tmp dir for both in-process and subprocess."""
    import terok_sandbox.port_registry as _reg

    registry = tmp_path / "terok-ports"
    registry.mkdir()
    monkeypatch.setattr(_reg._default, "registry_dir", registry)
    monkeypatch.setattr(_reg, "_save_ports", lambda _sd, _p: None)
    monkeypatch.setenv("TEROK_PORT_REGISTRY_DIR", str(registry))
    _reg._default.reset()


# ── Isolated terok CLI environment ────────────────────────


@pytest.fixture
def terok_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> TerokIntegrationEnv:
    """Return an isolated terok config/state environment for a test."""
    _reset_layered_config_caches()

    home_dir = tmp_path / HOME_DIR_NAME
    xdg_config_home = tmp_path / XDG_CONFIG_HOME_NAME
    system_config_root = tmp_path / CONFIG_ROOT_NAME
    state_root = tmp_path / STATE_ROOT_NAME

    for path in (home_dir, xdg_config_home, system_config_root, state_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("TEROK_CONFIG_DIR", str(system_config_root))
    monkeypatch.setenv("TEROK_STATE_DIR", str(state_root))

    # Subprocess-based tests spawn a fresh CLI process, so expose the
    # isolated test config through TEROK_CONFIG_FILE.  That keeps both terok
    # and terok-sandbox away from the real /etc/user config stack and also
    # avoids root/user-namespace path heuristics in nested CI containers.
    config_dir = xdg_config_home / "terok"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yml"
    config_lines = []
    if "needs_vault" not in {m.name for m in request.node.iter_markers()}:
        config_lines.extend(["vault:", "  bypass_no_secret_protection: true"])
    config_lines.extend(["credentials:", "  passphrase: integration-test-passphrase"])
    config_file.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(config_file))
    _reset_layered_config_caches()

    # The scrubbed HOME must not blind podman to the operator's container
    # storage configuration: on hosts where rootless podman *requires* a
    # per-user storage.conf (the matrix podman slot sets fuse-overlayfs as
    # the nested-overlay mount_program there), a child podman spawned by
    # the terok CLI under the tmp HOME would otherwise fail to configure
    # storage at all and every state query would report the runtime
    # unavailable.  Copy the real user's containers config into the tmp
    # XDG so terok's children keep the host's storage semantics while
    # terok's own state stays isolated.
    real_containers_conf = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config" / "containers"
    if real_containers_conf.is_dir():
        shutil.copytree(real_containers_conf, xdg_config_home / "containers", dirs_exist_ok=True)

    # Copying only helps hosts that HAVE a per-user storage.conf.  The
    # matrix podman slot has none: its working store exists only as
    # pre-initialized state under the real HOME, so a child podman under
    # the tmp HOME re-probes storage from scratch and dies ("'overlay'
    # is not supported over overlayfs, a mount_program is required").
    # Point the scrubbed env at the real, initialized store by asking
    # the ambient podman where it lives -- deriving (not hardcoding) the
    # driver keeps vfs-backed slots correct too.
    storage_conf = xdg_config_home / "containers" / "storage.conf"
    if not storage_conf.exists():
        probe = subprocess.run(
            [
                "podman",
                "info",
                "--format",
                "{{.Store.GraphDriverName}}|{{.Store.GraphRoot}}|{{.Store.RunRoot}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.count("|") == 2:
            driver, graphroot, runroot = probe.stdout.strip().split("|")
            storage_conf.parent.mkdir(parents=True, exist_ok=True)
            storage_conf.write_text(
                f'[storage]\ndriver = "{driver}"\ngraphroot = "{graphroot}"\n'
                f'runroot = "{runroot}"\n'
            )

    env = TerokIntegrationEnv(
        base_dir=tmp_path,
        home_dir=home_dir,
        xdg_config_home=xdg_config_home,
        system_config_root=system_config_root,
        state_root=state_root,
    )
    env.user_projects_root.mkdir(parents=True, exist_ok=True)
    env.system_projects_root.mkdir(parents=True, exist_ok=True)
    env.vault_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEROK_VAULT_DIR", str(env.vault_dir))
    agent_state = tmp_path / "agent-state"
    agent_state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEROK_EXECUTOR_STATE_DIR", str(agent_state))
    sandbox_live = tmp_path / "sandbox-live"
    sandbox_state = tmp_path / "sandbox-state"
    sandbox_runtime = tmp_path / "sandbox-runtime"
    sandbox_live.mkdir(parents=True, exist_ok=True)
    sandbox_state.mkdir(parents=True, exist_ok=True)
    sandbox_runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEROK_SANDBOX_LIVE_DIR", str(sandbox_live))
    monkeypatch.setenv("TEROK_SANDBOX_STATE_DIR", str(sandbox_state))
    # Redirect sandbox's runtime_dir too — otherwise SandboxConfig.runtime_dir
    # falls back to $XDG_RUNTIME_DIR/terok/sandbox and reads the operator's
    # *real* ``vault.passphrase`` (written by ``terok-sandbox vault unlock``).
    # The chain then prefers that over the test config's
    # ``credentials.passphrase``, so the broker decrypts the test DB with
    # the operator's real passphrase → ``file is not a database``.  Matrix
    # containers have no such file so this only bites on developer hosts
    # with an unlocked vault.
    monkeypatch.setenv("TEROK_SANDBOX_RUNTIME_DIR", str(sandbox_runtime))

    # Seed a valid setup.stamp so subprocess CLIs don't exit 3 at the
    # verdict gate on ``terok task run`` / ``terok-executor run``.  Real
    # operators run ``terok setup`` once before tasks; the integration
    # harness simulates that completed state without running the full
    # installer.  Tests that specifically exercise the gate's response
    # to FIRST_RUN / STALE_* verdicts should manipulate the stamp
    # themselves (or use unit tests which don't rely on this fixture).
    #
    # ``TEROK_ROOT`` is set via subprocess env in ``TerokIntegrationEnv.cli_env``
    # (helpers.py), so we also mirror it into the pytest process so the
    # in-process ``write_stamp()`` call below targets the same path.
    monkeypatch.setenv("TEROK_ROOT", str(tmp_path))
    from terok_sandbox.setup_stamp import write_stamp

    write_stamp()
    return env


# ── Vault ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _bypass_vault(request: pytest.FixtureRequest) -> Iterator[None]:
    """Bypass the vault unless the test explicitly needs it.

    Tests marked with ``needs_vault`` opt out of the bypass
    and exercise the real vault path.  All other tests get the bypass
    so they don't need a running vault daemon.

    For subprocess-based tests (via ``terok_env``), the bypass is written
    as a config file in the ``terok_env`` fixture itself.
    """
    if "needs_vault" in {m.name for m in request.node.iter_markers()}:
        # In the per-container-supervisor model the vault is not a host
        # daemon anymore — each container's supervisor embeds its own
        # proxy.  ``needs_vault`` tests exercise the in-container vault
        # path; nothing on the host needs patching, but the marker is
        # kept as an explicit opt-out from the default ``get_vault_bypass``
        # patch below.
        yield
    else:
        with patch(
            "terok.lib.core.config.get_vault_bypass",
            return_value=True,
        ):
            yield
