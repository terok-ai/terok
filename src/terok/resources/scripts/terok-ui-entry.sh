#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

# Reuse SSH + project repo init (if script exists)
if command -v /usr/local/bin/init-ssh-and-repo.sh >/dev/null 2>&1; then
  /usr/local/bin/init-ssh-and-repo.sh || exit $?
fi

# Set git author/committer based on UI backend for AI-generated commits
# Author = AI agent, Committer = Human (if configured)
# This ensures commits made by the UI are properly attributed
if command -v git >/dev/null 2>&1 && [[ -n "${TEROK_UI_BACKEND:-}" ]]; then
  case "${TEROK_UI_BACKEND,,}" in
    codex)
      export GIT_AUTHOR_NAME="Codex"
      export GIT_AUTHOR_EMAIL="codex@openai.com"
      ;;
    claude)
      export GIT_AUTHOR_NAME="Claude"
      export GIT_AUTHOR_EMAIL="noreply@anthropic.com"
      ;;
    copilot)
      export GIT_AUTHOR_NAME="GitHub Copilot"
      export GIT_AUTHOR_EMAIL="copilot@github.com"
      ;;
    mistral)
      export GIT_AUTHOR_NAME="Mistral Vibe"
      export GIT_AUTHOR_EMAIL="vibe@mistral.ai"
      ;;
    *)
      # Default fallback for unknown backends
      export GIT_AUTHOR_NAME="AI Agent"
      export GIT_AUTHOR_EMAIL="ai-agent@localhost"
      ;;
  esac
  
  # Set committer to human credentials
  export GIT_COMMITTER_NAME="${HUMAN_GIT_NAME:-Nobody}"
  export GIT_COMMITTER_EMAIL="${HUMAN_GIT_EMAIL:-nobody@localhost}"
fi

: "${TEROK_UI_DIR:=/opt/terok-web-ui}"
: "${HOST:=0.0.0.0}"
: "${PORT:=7860}"

ui_entry="${TEROK_UI_DIR}/dist/server.js"
if [[ ! -f "${ui_entry}" ]]; then
  echo "!! missing preinstalled Terok Web UI distribution (expected ${ui_entry})."
  echo "!! ensure the image build installs the Terok Web UI dist tarball."
  exit 1
else
  echo ">> using preinstalled Terok Web UI at ${ui_entry}"
fi
cd "${TEROK_UI_DIR}"

# If a task workspace repository exists, prefer that as working directory
if [[ -n "${REPO_ROOT:-}" && -d "${REPO_ROOT}" ]]; then
  echo ">> switching to repo root: ${REPO_ROOT}"
  cd "${REPO_ROOT}"
fi

# Always run the UI server from the CodexUI repo, even if the working
# directory is the task workspace. This ensures that dist/server.js is
# resolved from TEROK_UI_DIR while allowing the UI to treat the workspace as
# its current directory (for project-specific files, etc.).
if [[ -z "${TEROK_UI_LOG:-}" && ! -w /var/log ]]; then
  export TEROK_UI_LOG="/tmp/terok-web-ui.log"
fi
echo ">> starting UI on ${HOST}:${PORT}"
exec node "${ui_entry}"
