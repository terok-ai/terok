.PHONY: all lint format test test-unit test-fast ruff-report bandit-report sonar-inputs test-integration test-integration-host test-integration-network test-integration-podman test-integration-map test-matrix ci-map tach lint-imports security docstrings complexity deadcode reuse check install install-dev docs docs-build clean spdx typecheck

REPORTS_DIR ?= reports
COVERAGE_XML ?= $(REPORTS_DIR)/coverage.xml
COVERAGE_JSON ?= $(REPORTS_DIR)/coverage.json
UNIT_JUNIT_XML ?= $(REPORTS_DIR)/unit.junit.xml
INTEGRATION_HOST_JUNIT_XML ?= $(REPORTS_DIR)/integration-host.junit.xml
INTEGRATION_NETWORK_JUNIT_XML ?= $(REPORTS_DIR)/integration-network.junit.xml
INTEGRATION_PODMAN_JUNIT_XML ?= $(REPORTS_DIR)/integration-podman.junit.xml
INTEGRATION_JUNIT_XML ?= $(REPORTS_DIR)/integration.junit.xml
RUFF_REPORT ?= $(REPORTS_DIR)/ruff-report.json
BANDIT_REPORT ?= $(REPORTS_DIR)/bandit-report.json

all: check

# Run linter and format checker (fast, run before commits)
lint:
	@if LC_ALL=C grep -nP '[^\x00-\x7F]' pyproject.toml; then echo "pyproject.toml must be ASCII-only"; exit 1; fi
	mkdir -p $(REPORTS_DIR)
	uv run ruff check --exit-zero --output-format=json --output-file=$(RUFF_REPORT) .
	uv run ruff check .
	uv run ruff format --check .

# Auto-fix lint issues and format code
format:
	uv run ruff check --fix .
	uv run ruff format .

# Run tests with coverage (excludes integration tests)
test: test-unit

# Fast dev loop: run only the tests affected by the branch diff (tach
# impact analysis), no coverage.  Impact analysis follows the Python
# import graph only — after touching non-Python inputs (resources/,
# YAML, templates, scripts) run the full `make test` instead.
test-fast:
	uv run pytest tests/unit/ --tach

test-unit:
	mkdir -p $(REPORTS_DIR)
	uv run pytest tests/unit/ --cov=terok --cov-report=term-missing --cov-report=xml:$(COVERAGE_XML) --cov-report=json:$(COVERAGE_JSON) --junitxml=$(UNIT_JUNIT_XML) -o junit_family=legacy

# Write Ruff's JSON report without failing on findings.
ruff-report:
	mkdir -p $(REPORTS_DIR)
	uv run ruff check --exit-zero --output-format=json --output-file=$(RUFF_REPORT) .

# Write Bandit's JSON report without failing on findings.
bandit-report:
	mkdir -p $(REPORTS_DIR)
	uv run bandit -r src/terok/ --exit-zero -f json -o $(BANDIT_REPORT)

# Generate the files SonarQube Cloud imports from reports/.
sonar-inputs: test-unit ruff-report bandit-report

# Run integration tests (tier 2 auto-skips without podman)
test-integration:
	mkdir -p $(REPORTS_DIR)
	uv run pytest tests/integration/ -v --junitxml=$(INTEGRATION_JUNIT_XML) -o junit_family=legacy

# Run host-only integration tests (filesystem/process workflows; no podman/network)
# needs_hooks tests are skipped automatically when hooks are absent;
# hook installation happens only inside disposable matrix containers (terok-matrix).
test-integration-host:
	mkdir -p $(REPORTS_DIR)
	uv run pytest tests/integration/ -m "needs_host_features and not needs_internet and not needs_podman" -v --junitxml=$(INTEGRATION_HOST_JUNIT_XML) -o junit_family=legacy

# Run network integration tests (no podman)
test-integration-network:
	mkdir -p $(REPORTS_DIR)
	@status=0; \
	uv run pytest tests/integration/ -m "needs_internet and not needs_podman" -v --junitxml=$(INTEGRATION_NETWORK_JUNIT_XML) -o junit_family=legacy || status=$$?; \
	test $$status -eq 0 -o $$status -eq 5

# Run only podman integration tests (for local runs with podman)
test-integration-podman:
	mkdir -p $(REPORTS_DIR)
	uv run pytest tests/integration/ -m "needs_podman" -v --junitxml=$(INTEGRATION_PODMAN_JUNIT_XML) -o junit_family=legacy

# Generate integration test map (Markdown table grouped by directory)
test-integration-map:
	uv run python docs/test_map.py

# Multi-distro integration test matrix — slots declared in
# tests/containers/matrix.yml, engine provided by terok-util (terok-matrix).
#   NO_CACHE=1 make test-matrix           — force full image rebuild
#   BUILD_ONLY=1 make test-matrix         — build images only
#   SLOTS="debian12 fedora43" make test-matrix — run specific slots
#   JOBS=4 make test-matrix               — run up to N slots concurrently
# `make -j 4 test-matrix` works too: GNU make >= 4.3 exposes -jN in MAKEFLAGS,
# and JOBS defaults to it.  An explicit JOBS= always wins; bare -j (unlimited)
# carries no number and falls back to serial.
MAKE_JOBS = $(patsubst -j%,%,$(filter -j%,$(MAKEFLAGS)))
JOBS ?= $(MAKE_JOBS)
test-matrix:
	uv run terok-matrix \
		$(if $(NO_CACHE),--no-cache) \
		$(if $(BUILD_ONLY),--build-only) \
		$(if $(JOBS),--jobs $(JOBS)) \
		$(SLOTS)

# Generate CI workflow map (Markdown tables from .github/workflows/*.yml)
ci-map:
	uv run python docs/ci_map.py

# Check module boundary rules (tach.toml)
tach:
	uv run tach check

# Check cross-package import boundaries (.importlinter)
lint-imports:
	uv run lint-imports

# Run SAST scan on the terok source tree
security: bandit-report
	uv run bandit -r src/terok/ -ll

# Check docstring coverage (minimum 95%)
docstrings:
	uv run docstr-coverage src/terok/ --fail-under=95

# Check cognitive complexity (advisory — lists functions exceeding threshold)
complexity:
	uv run complexipy src/terok/ --max-complexity-allowed 15 --failed; true

# Find dead code (cross-file, min 80% confidence)
deadcode:
	uv run vulture src/terok/ vulture_whitelist.py --min-confidence 80

# Static type check with mypy.
typecheck:
	uv run mypy src/terok/ $(MYPYFLAGS)

# Check REUSE (SPDX license/copyright) compliance
reuse:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	uv run reuse lint

# Add SPDX header to files.
# NAME must be the real name of the person responsible for creating the file (not a project name).
# Example: make spdx NAME="Real Human Name" FILES="src/terok/new_file.py"
spdx:
ifndef NAME
	$(error NAME is required — use the real name of the copyright holder, e.g. make spdx NAME="Real Human Name" FILES="src/terok/new_file.py")
endif
	uv run reuse annotate --template compact --copyright "$(NAME)" --license Apache-2.0 $(FILES)

# Run all checks (equivalent to CI)
check: lint test tach lint-imports typecheck security docstrings deadcode reuse

# Install runtime dependencies only
install:
	uv sync --no-default-groups

# Install all dependencies (dev, test, docs)
install-dev:
	uv sync --group docs
	uv run pre-commit install

# Build documentation locally
docs:
	uv run properdocs serve

# Build documentation for deployment
docs-build:
	uv run properdocs build --strict

# Clean build artifacts
clean:
	rm -rf dist/ site/ reports/ .coverage coverage.xml .pytest_cache/ .ruff_cache/ .complexipy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
