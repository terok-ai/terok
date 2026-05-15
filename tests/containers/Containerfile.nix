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
# No ``util-linux`` / ``shadow``: their ``su`` / ``runuser`` need SUID
# (configured via NixOS security-wrappers, which the bare ``nixos/nix``
# image doesn't ship).  ``run_nix_tests`` switches users via Python's
# ``os.setuid`` instead.  When the rootless-podman follow-up lands
# we'll add ``shadow`` for ``newuidmap``/``newgidmap``.
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

# /bin/bash → the bash the base image already has, so shebangs and
# the testrunner login shell resolve.
RUN ln -s /nix/var/nix/profiles/default/bin/bash /bin/bash

# Non-root user (uid 1000) so terok-sandbox's ``systemd_user_unit_dir``
# guard doesn't refuse on us — that path bails when running as root,
# and several unit tests touch it.  Direct ``/etc/passwd`` write
# instead of useradd: nixos/nix ships none of the files shadow's
# userdb wants (``/etc/passwd``, ``/etc/login.defs``, the lastlog
# skeleton on the bind-mounted ``/nix/store``), and an echo into
# ``/etc/passwd`` is the same outcome with less ceremony.
RUN install -d /etc \
    && echo 'testrunner:x:1000:1000::/home/testrunner:/bin/bash' >> /etc/passwd \
    && echo 'testrunner:x:1000:' >> /etc/group \
    && install -d -o 1000 -g 1000 /home/testrunner /home/testrunner/.local/bin \
    && install -d -o 1000 -g 1000 /nix/var/nix/profiles/per-user/testrunner

# No USER directive — outer ``bash -c`` in run_nix_tests runs as root
# to do the workspace prep, then ``su - testrunner`` for the venv +
# tests.  See the dispatch in run-matrix.sh.
ENV PATH=/nix/var/nix/profiles/default/bin:$PATH
