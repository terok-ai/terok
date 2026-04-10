# Kernel Keyring Exhaustion

## Symptom

After starting and stopping many containers, `podman run` fails with:

```text
crun: create keyring `<container>`: Disk quota exceeded: OCI runtime error
```

This is **not** a disk space issue — the misleading message comes from the
kernel keyring subsystem returning `EDQUOT`.

## Root cause

The OCI runtime (`crun`) creates a new
[session keyring](https://man7.org/linux/man-pages/man7/keyrings.7.html)
for every container it starts.  These keyrings are not reliably cleaned up
when the container is removed, so they accumulate until the per-user quota
is exhausted.

Linux defaults to **200 keys** and **20 000 bytes** per non-root user
(`/proc/sys/kernel/keys/maxkeys` and `maxbytes`).  A busy terok host that
cycles many agent containers will hit this limit.

## Impact on terok containers

The per-container kernel keyring is used by subsystems that terok agent
containers never touch:

| Subsystem | Used by terok agents? |
|-----------|----------------------|
| Kerberos ticket cache | No |
| dm-crypt / LUKS | No |
| eCryptfs | No |
| IMA / EVM signing | No |
| Generic `keyctl` storage | No — seccomp blocks `keyctl` by default |

Kernel keyrings are also
[not namespaced](https://projectatomic.io/blog/2014/09/yet-another-reason-containers-don-t-contain-kernel-keyrings/) —
they are separated by UID only, not by container.  Rootless user namespaces
and seccomp (both active in terok) provide the real isolation.

Disabling keyring creation has no effect on terok's functionality.

## Workaround

Podman does not support disabling keyring creation per container — the
setting is global in `containers.conf`.  This means the workaround
affects **all** containers on the host, not just terok's.  If you run
other workloads that depend on kernel keyrings (Kerberos, dm-crypt, etc.),
evaluate the trade-off before applying.

Set `keyring = false` in the `[containers]` section of
`~/.config/containers/containers.conf` (create the file if it does not
exist).  This tells `crun` to skip the
`keyctl(KEYCTL_JOIN_SESSION_KEYRING)` call entirely, eliminating the leak:

```toml
[containers]
keyring = false
```

!!! tip "Sickbay detection"
    `terok sickbay` warns when keyring creation is not disabled.
    Run it after installation to verify your setup.

## References

- [containers/podman#13363](https://github.com/containers/podman/issues/13363) — original keyring leak report
- [containers/podman#23784](https://github.com/containers/podman/issues/23784) — recurring "Disk quota exceeded"
- [containers/podman#8384](https://github.com/containers/podman/issues/8384) — `keyring` config option request
- [containers.conf(5)](https://github.com/containers/common/blob/main/docs/containers.conf.5.md) — configuration reference
