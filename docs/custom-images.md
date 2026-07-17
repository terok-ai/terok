# Custom Base Images

Any OCI image can serve as a project's base (`image.base_image` in
`project.yml`, or the wizard's *Custom image…* option).  terok never
requires one of the bundled choices — the bundled list is a convenience,
not an allowlist.  This page explains exactly what terok does with a
base image, what it assumes about it, and the patterns for rolling your
own.

## What terok builds on top of your image

Your image is only the bottom of the stack — terok layers its own
images on top (see [Container Layers](container-layers.md)):

- **L0** (`FROM <your image>`) installs common dev tooling (git, ssh,
  sudo, ripgrep, vim, tmux, socat, locales/tzdata), creates the
  `dev` user (UID 1000) with passwordless sudo, and stages terok's
  in-container scripts (`init-ssh-and-repo.sh` and friends).
- **L1** (`FROM` L0) installs the selected AI agents.
- **L2** (`FROM` L1) applies project specifics, including your custom
  Dockerfile snippet.

Because the terok layers are always built on top, **you never need to
copy terok's init scripts or replicate its setup in a custom image** —
every base gets them automatically.  Equally, there is no supported way
to replace the L0/L1 definitions themselves; the customization points
are the base underneath and the snippet on top.

## Package-family autodetection

L0 must know whether to drive `apt` or `dnf`.  terok resolves the
family from the image name against a small allowlist (matching the
first path component of the name, case-insensitively, after stripping
any `:tag` or `@digest`):

| Image name prefix | Family |
|---|---|
| `ubuntu`, `debian` | `deb` |
| `fedora`, `registry.fedoraproject.org/fedora` | `rpm` |
| `quay.io/podman` | `rpm` |
| `nvidia`, `nvcr.io/nvidia` | by tag: `ubi`-tagged variants → `rpm`, everything else → `deb` |
| `rocm` | by distro marker in the *name* (`dev-almalinux-8` → `rpm`); default `deb` |
| `intel` | by distro marker in the name or tag (`…-rockylinux9` → `rpm`); default `deb` |

An explicit `docker.io/` (or `docker.io/library/`) qualifier is ignored
for matching, so `docker.io/ubuntu:24.04` and `ubuntu:24.04` resolve
identically.  Other registry prefixes do not match and need `family:`.

**Anything else must declare its family explicitly**, or the image
build fails with `Cannot infer package family`:

```yaml
image:
  base_image: rockylinux:9
  family: rpm
```

`family` also *overrides* detection when set, so a recognized name can
be forced the other way (rarely useful, but defined).

## What a custom base must provide

- A working `apt-get` (deb) or `dnf` (rpm) — L0's tooling install runs
  one of the two, per the resolved family.  Other package managers
  (apk, zypper, pacman) are not supported.
- Root during build (the standard Dockerfile default) so L0 can install
  packages and create the `dev` user.  A base whose `USER` is non-root
  will fail the L0 build.
- glibc — the locale setup expects it (musl-based images like Alpine
  are out, which the package-manager rule already implies).

UID 1000 being taken is fine: L0 detects an existing UID-1000 user and
repurposes it into `dev`.

## Patterns

### Near-blank base + snippet

A perfectly valid approach is picking the most minimal recognized base
and building the project environment entirely in the custom snippet —
uncommon, but fully supported:

```yaml
image:
  base_image: fedora:44
  user_snippet_file: user.dockerinclude
```

The snippet renders into L2, i.e. *after* the dev user and agents
exist.  Snippet commands run as root; use `su dev -c '…'` for steps
that must run as the container user.  Multi-`RUN` fragments, `ENV`
lines, and `ARG`s are all fine — the snippet is spliced into the L2
Dockerfile verbatim.

### Vendor stacks

GPU vendor images (CUDA, ROCm, oneAPI) are just custom bases with big
userlands — the wizard offers the common ones directly, and all three
vendors' images are on the autodetection allowlist above.  See
[GPU Passthrough](usage.md#gpu-passthrough) for granting the hardware.

### Baking the NVIDIA userland (toolkit-less hosts)

On raw-tier NVIDIA hosts (no Container Toolkit, no CDI) the image must
carry a driver userland matching the host kernel module.  A proven
snippet for `user_snippet_inline`/`user_snippet_file` — set `NV_DRIVER`
to the host's driver version (`nvidia-smi` shows it):

```dockerfile
ENV NV_DRIVER=595.71.05
RUN su dev -c 'cd /tmp \
        && curl -fLO https://us.download.nvidia.com/tesla/$NV_DRIVER/NVIDIA-Linux-x86_64-$NV_DRIVER.run \
        && sh NVIDIA-Linux-x86_64-$NV_DRIVER.run -x' \
    && cd /tmp/NVIDIA-Linux-x86_64-$NV_DRIVER \
    && install -D -m755 -t /usr/lib64/nvidia \
        libcuda.so.$NV_DRIVER libnvidia-ml.so.$NV_DRIVER libnvidia-ptxjitcompiler.so.$NV_DRIVER \
    && install -m755 nvidia-smi /usr/local/bin/ \
    && ldconfig -n /usr/lib64/nvidia \
    && ln -s libcuda.so.$NV_DRIVER /usr/lib64/nvidia/libcuda.so \
    && rm -rf /tmp/NVIDIA-Linux-x86_64-$NV_DRIVER* \
    && printf '%s\n' \
        'if [ -e /dev/nvidiactl ] && ! ldconfig -p | grep -q "libcuda\.so\.1"; then' \
        '    export LD_LIBRARY_PATH=/usr/lib64/nvidia${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}' \
        'fi' \
        > /etc/profile.d/nvidia-baked-fallback.sh
```

Notes: the profile.d guard prefers a host-injected userland when one
appears (installing the toolkit later needs no rebuild); the extraction
runs as `dev` deliberately — see
[Building on old podman hosts](#building-on-old-podman-hosts-seccomp);
and the baked version is **locked to the host driver** — a host driver
upgrade requires bumping `NV_DRIVER` and rebuilding.  Installing the
NVIDIA Container Toolkit (CDI) removes this whole requirement.

### Building on old podman hosts (seccomp)

Old podman releases (Ubuntu 22.04's 3.4 era) ship a seccomp profile
that returns `EPERM` — instead of `ENOSYS` — for syscalls newer than
the profile.  glibc falls back gracefully on `ENOSYS` but treats
`EPERM` as a real denial, so modern userlands inside the container can
fail in surprising, often root-only ways (observed: NVIDIA's `.run`
extractor dying in its permission sweep because root paths call
`fchmodat2`).  Per-user fix on such hosts: drop a current
[containers seccomp.json](https://github.com/containers/common/blob/main/pkg/seccomp/seccomp.json)
somewhere stable and point at it in
`~/.config/containers/containers.conf`:

```toml
[containers]
seccomp_profile = "/home/<you>/.config/containers/seccomp.json"
```

The profile is read at container *creation* — recreate containers (and
rerun builds) after changing it; restarts keep the old profile.
Workarounds when you cannot touch the host config: run the failing
build step as a non-root user (`su dev -c '…'`), whose code paths often
avoid the new syscalls.

### Private registries

Registry hosts and ports are handled (`localhost:5000/fedora:44`
parses; the family comes from the path component, so unrecognized
private paths need `family:`).  Authentication is podman's business —
`podman login` before the build.
