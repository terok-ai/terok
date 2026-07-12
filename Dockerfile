# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# terok-in-Docker — run terok inside a Docker container with nested
# rootless Podman.  Intended for local evaluation only.
#
# Quick start:
#   docker build -t terok-in-docker .
#   docker run -d --privileged --network host --name terok terok-in-docker
#   # open http://localhost:8566
#
# Full documentation: docs/docker.md
#

FROM quay.io/podman/stable

LABEL maintainer="terok maintainers"
LABEL org.opencontainers.image.description="terok with nested rootless Podman — try terok without installing Podman on the host"
LABEL org.opencontainers.image.source="https://github.com/terok-ai/terok"

# ── 1. System packages ──────────────────────────────────────────
RUN dnf install -y \
        python3 \
        python3-pip \
        python3-devel \
        git \
        nftables \
    && dnf clean all

# ── 2. Install terok from local source tree ──────────────────────
# The COPY has no .git, so hatch-vcs resolves the version from
# [tool.hatch.version] fallback-version.
COPY . /opt/terok-src
RUN pip install --break-system-packages /opt/terok-src \
    && rm -rf /opt/terok-src

# ── 3. Prepare terok directories and shell completions ────────────
# Switch to the image's podman user (uid 1000) for all user-space
# setup — directories are created with correct ownership naturally.
USER podman
RUN mkdir -p \
        ~/.config/terok \
        ~/.config/containers \
        ~/.cache/terok \
        ~/.cache/containers \
    && printf '%s\n' \
        'unqualified-search-registries = ["docker.io"]' \
        > ~/.config/containers/registries.conf \
    && terok completions install --shell bash
USER root

# ── 4. Inline entrypoint ──────────────────────────────────────────
# Runs as root (the image default): fixes bind-mount ownership,
# then drops to the podman user via su -m (preserving env vars).
RUN printf '%s\n' '#!/bin/sh' \
        'set -e' \
        'for d in /home/podman/.config/terok /home/podman/.local/share/terok /home/podman/.local/share/containers; do' \
        '    mkdir -p "$d"; chown -R podman:podman "$d"' \
        'done' \
        'if [ $# -gt 0 ]; then exec su -m podman -s /bin/sh -c '"'"'exec "$@"'"'"' sh "$@"; fi' \
        'exec su -m podman -s /bin/sh -c "exec terok-web --host 0.0.0.0 --port 8566 ${TEROK_PUBLIC_URL:+--public-url \"$TEROK_PUBLIC_URL\"}"' \
        > /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /home/podman
EXPOSE 8566

ENV HOME=/home/podman

ENTRYPOINT ["docker-entrypoint.sh"]
