# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Nix matrix slot.  Not a "distro" — the only goal is to reproduce a
# wrapped-Python setup so we can assert that ``[sys.executable, '-m',
# 'terok.cli.main', ...]`` re-entry still works (terok-ai/terok#717).
#
# We don't package terok as a Nix derivation; we just install it with
# pip inside a ``nix profile``-installed Python, which is enough to
# get the Nix wrapping behaviour on the interpreter.  Building a full
# nixpkgs derivation for terok-* siblings is a separate, much larger
# project.

FROM docker.io/nixos/nix:latest

# Pre-populate the system profile with what the tests need at runtime:
# wrapped python + pip and awk.  Bash and git-minimal already ship in
# the base image's profile; adding ``nixpkgs#bash`` or ``nixpkgs#git``
# here conflicts on shared files (``bash/printenv``, ``bin/git-shell``).
#
# ``--extra-experimental-features`` turns on flakes (off by default in
# nix 2.18-).
RUN nix --extra-experimental-features 'nix-command flakes' \
        profile add \
        nixpkgs#gawk

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
        '(builtins.getFlake "nixpkgs").legacyPackages.${builtins.currentSystem}.python312.withPackages (ps: [ ps.pip ])'

# /bin/bash → the bash the base image already has, so shebangs resolve.
RUN ln -s /nix/var/nix/profiles/default/bin/bash /bin/bash

# Run as root.  The wrapped-Python failure mode this slot exercises is
# uid-independent (the Nix wrapper rewrites sys.path the same way for
# any uid).  When podman gets added to this slot later, that's when a
# proper non-root user setup is needed for rootless operation —
# bringing in ``shadow``/``util-linux`` for ``newuidmap``/``su`` and
# the per-user nix profile dance.  Until then, root-only keeps the
# image lean and avoids the user-switching plumbing entirely.
ENV PATH=/nix/var/nix/profiles/default/bin:$PATH
