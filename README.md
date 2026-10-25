# agent-devbox

Run AI coding agents (Claude Code, Codex, T3 Code, Hermes Agent, …) on a
Linux dev server **without handing them your secrets or your network**.

agent-devbox is an Ansible playbook. Point it at a server, store your tokens
once, and the agents work as usual: they code, install dependencies, run
tests and servers, push to your repositories. What they cannot do is read
your credentials or send anything to a destination you did not allow.

## How it works, in one minute

The server is split into three **zones**:

| Zone | Who | Holds |
|---|---|---|
| **admin** | your existing login | sudo, SSH keys, your files: unchanged |
| **agent** | a dedicated `agent` user running the agents | code, toolchains, *placeholder* tokens only |
| **proxy** | [iron-proxy](https://github.com/ironsh/iron-proxy), its own system user | the real tokens |

```
agent processes ──HTTPS_PROXY──► iron-proxy ──► allowlisted hosts only
       │                           │  placeholder token → real token
       │ direct :443/:80 ──────────┘  (transparent redirect)
       │ any other traffic ──► nftables: rejected and logged
       └ localhost ──► only its own processes, the proxy, ports you share
```

- **Placeholders, not secrets.** The agent's environment holds fake tokens
  (`CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`, your API keys…). iron-proxy replaces
  them with the real values on the way out, only for the hosts you name. A
  stolen placeholder is worthless.
- **Default-deny egress.** A firewall rule per user sends the agent's traffic
  through iron-proxy, which only lets through what your allowlist permits,
  by host, method and path. Package registries are read-only.
- **Observe, then enforce.** Start in `observe` mode: nothing is blocked,
  everything that would be is logged. Review it in Grafana, then switch to
  `enforce`.

[docs/architecture.md](docs/architecture.md) explains each piece in detail.

## What the agent can do

| | |
|---|---|
| ✅ Call Claude / Codex | with your **subscriptions** (no API keys needed) |
| ✅ Use API-key providers | OpenRouter, OpenAI, Gemini, Groq…: the key stays in the proxy |
| ✅ Install dependencies | npm, pip, cargo, go, maven, gradle, gems, composer, nuget… (presets) |
| ✅ Install system packages | `sudo agent-pkg-install <pkg>`, official distro repositories only |
| ✅ Use git and GitHub | clone anything public or allowed, push to the repos you list, `gh pr create` |
| ✅ Run servers and tests | on any port, reachable from the host; from outside only on ports you open |
| ✅ Query its own logs | through a read-only gateway (why was that request blocked?) |
| ❌ Read your secrets | not in its zone; the proxy's files are not readable by it |
| ❌ Reach arbitrary hosts | only the allowlist, plus non-HTTP exceptions you declare |
| ❌ Reach your other local services | IDE servers, admin APIs, databases of other users |
| ❌ Become root | no sudo except the two helpers above, which take no options |

Everything outside the allowlist is a `403` from the proxy. Adding a domain
is one line in your config (see [Operations](docs/operations.md)).

## Requirements

- **Server**: Linux with systemd, cgroup v2 and OpenSSH ≥ 8.2, on x86_64 or
  aarch64. Tested on Debian 13, Ubuntu 24.04 and Rocky Linux 9; expected to
  work on Debian 12, Ubuntu 22.04, AlmaLinux/RHEL 9 and Fedora. A fresh
  server or an existing one: your account and files are left alone. macOS
  and Windows are not supported as servers.
- **Access**: an SSH key login to the server for a user with sudo. This user
  is your `devbox_admin_user`. Run the playbook as that user, without `sudo`
  in front: it becomes root on the server by itself.
- **Control machine** (where you run the playbook): Linux, macOS, or Windows
  with WSL, Python ≥ 3.12, and ansible-core 2.20 or 2.21.
- A [Claude](https://claude.ai) subscription and/or a ChatGPT plan for
  Codex, and optionally a GitHub account.

## Quick start

**1. Get the playbook** on your control machine:

```bash
git clone https://github.com/Quertyy/agent-devbox && cd agent-devbox
python3 -m venv .venv && .venv/bin/pip install 'ansible-core>=2.20,<2.22'
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

**2. Describe your server** in `inventory/` (git-ignored, keep it private):

```bash
mkdir -p inventory/host_vars
cat > inventory/hosts.yml <<'EOF'
devbox:
  hosts:
    mybox: { ansible_host: mybox.example.net, ansible_user: alice }
EOF
cp config.example.yml inventory/host_vars/mybox.yml
```

Edit `inventory/host_vars/mybox.yml`: set at least `devbox_admin_user: alice`
and your SSH key in `devbox_agent_ssh_keys`, then add the presets of your stack.

**3. Run it** (5 to 10 minutes on a fresh server, most of it downloads):

```bash
.venv/bin/ansible-playbook site.yml        # add -K if sudo asks for a password
```

The end of the run lists the secrets that are not stored yet, and how to
reach Grafana.

**4. Store your tokens** on the server, in **your own** SSH session (never
in an agent session):

```bash
# on any machine with Claude Code (your laptop is fine):
claude setup-token                          # prints sk-ant-oat01-…, valid ~1 year
# on the server:
sudo iron-proxy-set-secret claude_oauth     # paste it; hidden, validated, applied at once
sudo iron-proxy-set-secret github_pat       # fine-grained PAT, see below
```

For GitHub, create a [fine-grained token](https://github.com/settings/personal-access-tokens)
limited to the repositories in `devbox_github_write_repos`, with *Contents*
and *Pull requests* set to read and write (*Metadata* read is added automatically).

**5. Connect your tools as the agent**: `ssh agent@mybox` (with a key from
`devbox_agent_ssh_keys`), or set the SSH user to `agent` in your IDE
(enable the `vscode` preset) or in T3 Code. Log Codex in once from that
session: `codex login --device-auth`. Work on the shared workspace **as the
agent**, never as your admin account: it is agent-controlled (see
[SECURITY.md](SECURITY.md#the-shared-workspace-belongs-to-the-agent)).

**6. Check the confinement:**

```bash
.venv/bin/ansible-playbook verify.yml      # PASS/FAIL per check, as the agent
```

**7. Work normally for a few days**, then open the *agent-devbox / Egress*
dashboard. Add to your config what is legitimately missing, set
`devbox_mode: enforce`, and run steps 3 and 6 again.

> **Always run the playbook from a checkout only you can modify.** It runs as
> root on the server: a copy the agent can write to (e.g. inside the shared
> workspace) would let it run code as root on your next run. The playbook
> refuses such a checkout.

## What the playbook changes on the server

- Creates the `agent` user (password locked, groups reset to the share
  group), the `iron-proxy` system user, the `devshare` group, and the shared
  `/srv/work` workspace. Your admin account only joins `devshare`.
- Installs iron-proxy and the monitoring stack from **pinned, checksummed**
  releases, as hardened systemd services listening on 127.0.0.1 only.
- Loads an nftables table (`inet agent_egress`) next to your existing
  firewall (ufw, firewalld and Docker rules are not touched).
- Disables SSH password and root login for every user (`devbox_harden_sshd`;
  refused if you have no SSH key).
- Adds a sudoers rule for the agent's two helpers, and nothing else.

The full list of files and units is in [docs/architecture.md](docs/architecture.md);
[docs/uninstall.md](docs/uninstall.md) removes everything.

## Documentation

| | |
|---|---|
| [Architecture](docs/architecture.md) | zones, request flow, firewall, files and services installed |
| [Agents](docs/agents.md) | Claude Code, Codex, T3 Code, Hermes Agent: setup and limits |
| [Configuration](docs/configuration.md) | every setting, its default, and the presets |
| [Operations](docs/operations.md) | secrets, adding domains, enforce mode, upgrades, CA rotation |
| [Troubleshooting](docs/troubleshooting.md) | a request or a tool fails: where to look, what to change |
| [FAQ](docs/faq.md) | containers, Java/TLS, API keys, other git forges… |
| [Uninstall](docs/uninstall.md) | remove agent-devbox from a server |
| [Security model](SECURITY.md) | what is protected, and what is not |
| [Contributing](CONTRIBUTING.md) | tests, linting, adding a distribution |

## License

Apache-2.0, like iron-proxy.
