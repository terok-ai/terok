#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Setup port forwarding for codex auth (port 1455)
# This script configures port forwarding for OAuth callbacks using socat.
# In rootless podman, we need to forward from the container IP to localhost.

set -e

echo '>> Setting up port forwarding for codex auth (port 1455)'

# Install required packages if not present
if ! command -v socat >/dev/null 2>&1 || ! command -v ip >/dev/null 2>&1; then
  echo '>> Installing required packages...'
  sudo apt-get update -qq
  sudo apt-get install -y socat iproute2
else
  echo '>> Required packages already installed'
fi

# Get container IP address
echo '>> Getting container IP address...'
CIP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)

if [ -z "$CIP" ]; then
  echo '>> ERROR: Could not detect container IP address'
  exit 1
fi

echo ">> Container IP: $CIP"

# Start socat port forwarder in background
echo '>> Starting socat port forwarder in background...'
socat -v TCP-LISTEN:1455,bind=$CIP,fork,reuseaddr TCP:127.0.0.1:1455 &
SOCAT_PID=$!
echo ">> socat running (PID: $SOCAT_PID)"

# Run codex login
echo '>> Starting codex login...'
codex login
EXIT_CODE=$?

# Clean up socat
echo '>> Stopping socat...'
kill $SOCAT_PID 2>/dev/null || true

exit $EXIT_CODE
