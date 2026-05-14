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

# Pre-populate the wrapped python + pip + shadow utilities in the
# system profile so the test container has them ready without a
# network round-trip per matrix run.  The base image already ships
# ``bash`` and ``git-minimal`` — adding ``nixpkgs#git`` here conflicts
# on ``bin/git-shell``, and we don't need git anyway (source tree
# arrives via bind-mount, not a clone).
#
# ``shadow`` provides ``useradd`` / ``groupadd``: the slot itself
# doesn't strictly need a non-root user (the wrapped-Python failure
# mode is uid-independent), but rootless podman *does*, and we want
# this image ready for that follow-up without a second image change.
#
# ``--extra-experimental-features`` turns on flakes (off by default in
# nix 2.18-).
RUN nix --extra-experimental-features 'nix-command flakes' \
        profile install \
        nixpkgs#python312 \
        nixpkgs#python312Packages.pip \
        nixpkgs#shadow

# Non-root user (uid 1000) — parity with the other matrix slots, and
# the prerequisite for rootless podman if/when this image starts
# exercising it.  ``--no-log-init`` is mandatory: the default tries to
# allocate the entire 32-bit lastlog file via ``ftruncate``, which
# fails on the bind-mounted /nix/store mountpoint with EINVAL.
RUN useradd --no-log-init -m -u 1000 -s /bin/bash testrunner \
    && mkdir -p /home/testrunner/.local/bin \
    && chown -R testrunner:testrunner /home/testrunner

# Each user gets their own per-user nix profile; pip --user etc. write
# under it.
RUN mkdir -p /nix/var/nix/profiles/per-user/testrunner \
    && chown testrunner:testrunner /nix/var/nix/profiles/per-user/testrunner

USER testrunner
ENV USER=testrunner HOME=/home/testrunner
ENV PATH=/home/testrunner/.local/bin:/nix/var/nix/profiles/default/bin:$PATH

WORKDIR /workspace
