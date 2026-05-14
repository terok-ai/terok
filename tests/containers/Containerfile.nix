# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Nix matrix slot.  Not a "distro" — the only goal is to reproduce a
# wrapped-Python setup so we can assert that ``[sys.executable, '-m',
# 'terok.cli.main', ...]`` re-entry still works (terok-ai/terok#717,
# Franz Pöschel's fix).
#
# We don't package terok as a Nix derivation; we just install it with
# pip inside a ``nix profile``-installed Python, which is enough to
# get the Nix wrapping behaviour on the interpreter.  Building a full
# nixpkgs derivation for terok-* siblings is a separate, much larger
# project.

FROM docker.io/nixos/nix:latest

# Single-user nix install in the base image runs as root.  Enable
# unprivileged user namespaces and pre-populate ``python3`` (with pip)
# + ``git`` in the system profile so the test container has them ready
# without a network round-trip per matrix run.
#
# ``--accept-flake-config`` keeps the build hermetic; ``--extra-experimental-features``
# turns on flakes (off by default in nix 2.18-).
RUN nix --extra-experimental-features 'nix-command flakes' \
        profile install \
        nixpkgs#python312 \
        nixpkgs#python312Packages.pip \
        nixpkgs#git \
        nixpkgs#bash

# Non-root user (uid 1000) so the container exercises the same "regular
# user shell" path Franz hit, not the root path.  Nix needs the user's
# home to exist for ~/.local writes from pip --user.
RUN useradd -m -u 1000 -s /bin/bash testrunner \
    && mkdir -p /home/testrunner/.local/bin \
    && chown -R testrunner:testrunner /home/testrunner

# Each user needs their own nix profile to install user-local packages
# without root.  Initialise the per-user profile dir.
RUN mkdir -p /nix/var/nix/profiles/per-user/testrunner \
    && chown testrunner:testrunner /nix/var/nix/profiles/per-user/testrunner

USER testrunner
ENV USER=testrunner HOME=/home/testrunner
ENV PATH=/home/testrunner/.local/bin:/nix/var/nix/profiles/default/bin:$PATH

WORKDIR /workspace
