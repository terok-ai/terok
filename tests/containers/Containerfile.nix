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

# Pre-populate the system profile with what the tests need at
# runtime: wrapped python + pip, awk, shadow (for rootless podman's
# newuidmap/newgidmap if/when this slot starts exercising podman).
#
# Bash and git-minimal already ship in the base image's profile;
# adding ``nixpkgs#bash`` or ``nixpkgs#git`` here conflicts on shared
# files (``bash/printenv``, ``bin/git-shell``).
#
# ``--extra-experimental-features`` turns on flakes (off by default in
# nix 2.18-).
RUN nix --extra-experimental-features 'nix-command flakes' \
        profile add \
        nixpkgs#gawk \
        nixpkgs#shadow

# ``python312`` and ``python312Packages.pip`` are *separate* derivations
# that don't share a site-packages; installing them side-by-side leaves
# ``python3.12 -m pip`` unable to find pip.
# ``python312.withPackages(ps: [ ps.pip ])`` builds a wrapped python
# whose sys.path includes the listed packages — what we actually want.
#
# That expression isn't a valid flakeref attrpath (``profile add`` only
# parses dot-separated names there), so feed it via ``--expr``.
# ``--impure`` is required by ``builtins.getFlake``.
RUN nix --extra-experimental-features 'nix-command flakes' \
        profile add --impure --expr \
        '(builtins.getFlake "nixpkgs").legacyPackages.x86_64-linux.python312.withPackages (ps: [ ps.pip ])'

# /bin/bash → the bash the base image already has, so shebangs and
# shadow's shell checks resolve.
RUN ln -s /nix/var/nix/profiles/default/bin/bash /bin/bash

# Non-root user (uid 1000) — parity with the other matrix slots, and
# the prerequisite for rootless podman if/when this image starts
# exercising it.  Skipping ``useradd``: nixos/nix ships none of the
# files shadow's userdb wants (``/etc/passwd``, ``/etc/login.defs``,
# the lastlog skeleton on the bind-mounted ``/nix/store``), and a
# direct ``/etc/passwd`` line is the same outcome with less ceremony.
RUN install -d /etc \
    && echo 'testrunner:x:1000:1000::/home/testrunner:/bin/bash' >> /etc/passwd \
    && echo 'testrunner:x:1000:' >> /etc/group \
    && install -d -o 1000 -g 1000 /home/testrunner /home/testrunner/.local/bin

# Each user gets their own per-user nix profile; pip --user etc. write
# under it.
RUN install -d -o 1000 -g 1000 /nix/var/nix/profiles/per-user/testrunner

USER testrunner
ENV USER=testrunner HOME=/home/testrunner
ENV PATH=/home/testrunner/.local/bin:/nix/var/nix/profiles/default/bin:$PATH

WORKDIR /workspace
