# Adding a distribution

Distribution differences live in two places:

- `roles/common/vars/<name>.yml`, looked up as
  `<Distribution>-<major>.yml`, then `<Distribution>.yml`, then
  `<os_family>.yml` (for example `Fedora.yml` overrides `RedHat.yml`);
- the package backend branch in
  `roles/agent_helpers/templates/agent-pkg-install.j2` (`apt` or `dnf` today),
  plus the official sources for `apt` in `agent-pkg.sources.j2`.

Everything else (iron-proxy, monitoring binaries, nftables policy, systemd
units) is distribution-agnostic.

## Requirements

systemd as init and user manager (`pam_systemd`, linger), cgroup v2,
nftables with `socket cgroupv2` support (Linux ≥ 5.13, nftables ≥ 1.0),
OpenSSH ≥ 8.2 with `/etc/ssh/sshd_config.d`, Python 3.

## Steps (example: Arch Linux, `os_family` Archlinux)

1. Create `roles/common/vars/Archlinux.yml` with:
   - `__devbox_pkg_backend` (e.g. `pacman`);
   - `__devbox_base_packages` (same tools as `Debian.yml`, with the
     distribution's names, including its `pam_systemd` package);
   - `__devbox_system_ca_bundle`: the real file, not a symlink (a systemd
     path unit watches it);
   - `__devbox_ssh_service`: the sshd unit name.
2. Allow the family in the first assertion of `roles/common/tasks/main.yml`
   and the backend in the "distribution settings are complete" assertion.
3. Add the backend branch to `agent-pkg-install.j2`. It must install from
   official repositories only, check signatures, refuse to remove or
   downgrade, never take options from the caller, and run from `/`.
4. If Tailscale is supported there, add its repository in
   `roles/tailscale/tasks/main.yml`.
5. Add a base image to `BASE` in `tests/run.sh`, a host to
   `tests/inventory.yml`, the name to the CI matrix in
   `.github/workflows/ci.yml`, and make `tests/run.sh <name>` pass.
