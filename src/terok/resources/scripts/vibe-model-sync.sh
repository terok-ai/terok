#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Mistral Model Sync Wrapper Script
# This script is designed to be called from bashrc to check for new Mistral models

set -euo pipefail

# Find the Python script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/mistral-model-sync.py"

# Check if the Python script exists
if [[ ! -f "${PYTHON_SCRIPT}" ]]; then
    echo "Mistral model sync: Python script not found at ${PYTHON_SCRIPT}" >&2
    exit 1
fi

# Run the Python script with appropriate arguments
# Use --min-age-hours to avoid hitting the API too frequently
# Only show output if there are changes (exit code != 0)
if ! python3 "${PYTHON_SCRIPT}" --min-age-hours 24; then
    # Script detected changes, show notification
    echo ""
    echo "==================================================================="
    echo "  Mistral Model Update Available"
    echo "==================================================================="
    echo ""
    echo "New models are available! Run the following to update your cache:"
    echo "  vibe-model-sync --ack"
    echo ""
    echo "==================================================================="
    echo ""
fi