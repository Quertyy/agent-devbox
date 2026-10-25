# Contributing

Issues and pull requests are welcome. Security problems: see
[SECURITY.md](SECURITY.md#reporting-a-vulnerability).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install 'ansible-core>=2.20,<2.22' ansible-lint pytest pyyaml
.venv/bin/ansible-galaxy collection install -r requirements.yml
.venv/bin/ansible-galaxy collection install -r tests/requirements.yml
```

## Checks

Run them before opening a pull request; CI runs the same ones.

```bash
.venv/bin/ansible-lint                         # production profile
.venv/bin/python -m pytest tests/unit          # filter and test plugins
.venv/bin/python tools/gen-config-doc.py --check
shellcheck tests/run.sh verify/agent-checks.sh roles/iron_proxy/files/iron-proxy-reload
tests/run.sh debian                            # integration (rootless podman, ~15 min)
```

`tests/run.sh` builds a systemd container per distribution, converges it in
observe then enforce mode, runs `verify.yml` after each, checks that a
second run and a `--check` run report no change, then uninstalls. `KEEP=1`
keeps the container for debugging (`podman exec -it devbox-test-debian bash`).

## Conventions

- **Settings** are `devbox_*`, documented in
  `roles/common/meta/argument_specs.yml` with their default in
  `roles/common/defaults/main.yml`. After changing either, regenerate
  `docs/configuration.md` with `tools/gen-config-doc.py`.
- **Internal variables** are `__devbox_*` (shared) or `__<role>_*` (one role).
- **Security-relevant logic** (the iron-proxy configuration, CIDR handling)
  lives in `filter_plugins/devbox.py` and is unit-tested.
- Roles declare their dependencies in `meta/main.yml`; every role depends on
  `common`, directly or not. Do not put `tags` on roles in `site.yml`: they
  defeat the deduplication of dependencies and roles would run several times.
- Inside the agent's home, tasks run as the agent (`become_user`), never as
  root: the agent controls that directory.
- Facts shared between roles: `__devbox_arch` and `__devbox_secrets`
  (common), `__devbox_agent_uid` and `__devbox_agent_home` (zones),
  `__devbox_grafana_public_url` (tailscale, read by monitoring). Allowlist
  presets live in `roles/iron_proxy/vars/` on purpose (not overridable: add
  rules with `devbox_allow_rules_extra` instead).
- Tasks must be idempotent and support `--check`.
- Pinned downloads always come with their sha256.
- Examples stay domain-neutral (an API key, a database, an internal HTTP API).

## Adding a distribution

See [docs/adding-a-distro.md](docs/adding-a-distro.md).
