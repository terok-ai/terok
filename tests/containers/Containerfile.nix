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

# Pre-populate the wrapped python + pip in the system profile so the
# test container has them ready without a network round-trip per matrix
# run.  The base image already ships ``bash`` and ``git-minimal`` —
# adding ``nixpkgs#git`` here conflicts on ``bin/git-shell``, and we
# don't need git anyway (the source tree arrives via bind-mount, not a
# clone).
#
# ``--extra-experimental-features`` turns on flakes (off by default in
# nix 2.18-).
RUN nix --extra-experimental-features 'nix-command flakes' \
        profile install \
        nixpkgs#python312 \
        nixpkgs#python312Packages.pip

# Run as root.  ``nixos/nix`` is minimal — no ``shadow`` / ``useradd`` —
# and the wrapped-Python failure mode this slot exists to catch is uid-
# independent (the wrapper rewrites sys.path the same way for any uid).
# Pulling in ``nixpkgs#shadow`` just to drop privileges would add ~30 MB
# of nix-store paths for no test-shape gain.
ENV PATH=/root/.local/bin:/nix/var/nix/profiles/default/bin:$PATH

WORKDIR /workspace
