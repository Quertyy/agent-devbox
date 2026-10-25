# Operations

Day-to-day tasks. "On the server" means in **your own** SSH session as the
admin, never in an agent session. Configuration changes always go through
your host file and `ansible-playbook site.yml` on the control machine: the
generated files on the server are overwritten at the next run.

- [Store or rotate a secret](#store-or-rotate-a-secret)
- [Allow a new site](#allow-a-new-site)
- [Use another API key from the agent's code](#use-another-api-key-from-the-agents-code)
- [Switch from observe to enforce](#switch-from-observe-to-enforce)
- [Get a shell as the agent](#get-a-shell-as-the-agent)
- [Grafana](#grafana)
- [Reload, restart, status](#reload-restart-status)
- [Upgrade](#upgrade)
- [Rotate the CA](#rotate-the-ca)
- [Tighten the admin side](#tighten-the-admin-side)

## Store or rotate a secret

On the server:

```bash
sudo iron-proxy-set-secret <name>
```

The value is read from a hidden prompt (or stdin), checked against the
expected format when one is known, stored in `/etc/iron-proxy/secrets/<name>`
and applied at once. Only declared names are accepted: `claude_oauth`,
`github_pat` and every `devbox_secrets[].name`.

- **Claude**: run `claude setup-token` wherever you have Claude Code (it
  prints a `sk-ant-oat01-…` token valid about a year), then store it as
  `claude_oauth`. The *Host* dashboard shows its age; the
  `DevboxClaudeTokenAging` alert fires after 330 days.
- **GitHub**: a [fine-grained token](https://github.com/settings/personal-access-tokens)
  limited to the repositories in `devbox_github_write_repos`, with
  *Contents* and *Pull requests* read and write. Editing the token's
  permissions on GitHub does not change its value: no need to store it again.

The playbook reports declared secrets that are not stored yet, and stored
files that are no longer declared (delete those yourself).

## Allow a new site

The agent gets a `403` (enforce) or the request shows up as *would be
blocked* (observe). In your host file, either enable a preset:

```yaml
devbox_allow_presets: [anthropic, openai_codex, github_read, npm, maven]
```

or add a rule, as narrow as you can:

```yaml
devbox_allow_rules_extra:
  - { host: "api.example.com" }                       # any method and path
  - host: "docs.example.com"
    methods: [GET]
    paths: ["/v2/*"]
```

Then run `site.yml` again; allowlist and secret changes are applied without
dropping connections (deny-list, port and TLS changes restart iron-proxy).
[docs/configuration.md](configuration.md#presets) lists the presets.

## Use another API key from the agent's code

Declare it, and give the code the placeholder instead of the key:

```yaml
devbox_secrets:
  - name: acme_api_key
    proxy_value: "acme-placeholder-0000000000"   # goes in the project's .env
    match_headers: ["X-Api-Key"]
    hosts: ["api.acme.example"]
    allow: true
```

Run `site.yml`, then on the server `sudo iron-proxy-set-secret acme_api_key`.
Use `match_path` or `match_query` when the key travels in the URL.

## Switch from observe to enforce

1. Work normally for a few days in `observe` mode.
2. Open Grafana, dashboard *agent-devbox / Egress*: the *Hosts outside the
   allowlist* table lists what would be blocked. Allow what is legitimate;
   telemetry and anything unexpected can stay blocked.
3. Set `devbox_mode: enforce` in your host file, run `site.yml`, then
   `verify.yml`.

To go back, set `devbox_mode: observe` and run `site.yml` again. In an
emergency, on the server, `sudo systemctl stop agent-egress-firewall` removes
the firewall table (the proxy allowlist still applies to proxied traffic).

## Get a shell as the agent

```bash
ssh agent@<host>              # from your laptop, with a key in devbox_agent_ssh_keys
ssh agent@localhost           # from the server
```

**Open the workspace in your IDE as the agent too** (Remote-SSH to
`agent@<host>`, with the `vscode` preset): the workspace is agent-controlled,
and IDEs run git and project tasks automatically. See
[SECURITY.md](../SECURITY.md#the-shared-workspace-belongs-to-the-agent).

Avoid `sudo -u agent` / `sudo -iu agent`: the shell keeps your session's
cgroup, so the environment is missing and, in enforce mode, the agent's own
local servers are refused.

## Grafana

Bound to `127.0.0.1:3000`. Reach it with
`ssh -L 3000:127.0.0.1:3000 <admin>@<host>` and open http://localhost:3000,
or over your tailnet when Tailscale is enabled
(`https://<host>.<tailnet>.ts.net`). User `admin`; the password is in
`/etc/agent-devbox/grafana.env` on the server (applied on Grafana's first
start only: change it in Grafana afterwards).

Dashboards: *agent-devbox / Egress* (proxied requests, would-be-blocked
hosts, secret swaps, firewall hits, helper usage) and *agent-devbox / Host*
(confinement services, secrets, CA, resources). Alerts are listed under
*Alerting*; no notification channel is configured, add one in Grafana if you
want them by mail or chat.

## Reload, restart, status

On the server:

```bash
systemctl status iron-proxy agent-egress-firewall 'devbox-*'
sudo systemctl reload iron-proxy               # re-read allowlist and secrets, no downtime
sudo systemctl restart agent-egress-firewall   # re-apply the nftables policy
sudo journalctl -u iron-proxy -f               # audit log, one JSON line per request
sudo journalctl -k -f | grep -E '(agent|proxy)-egress:'   # firewall refusals
```

## Upgrade

**agent-devbox itself**: `git pull` in your checkout, read the diff (the
playbook runs as root on your servers), read [CHANGELOG.md](../CHANGELOG.md),
then run `site.yml --check --diff` and `site.yml`. (`--check` works on a
server that has been converged once.) Agent services that run permanently
(`systemctl --user`) pick up environment changes when they restart.

**Pinned components** (iron-proxy, Prometheus, Loki, Alloy, Grafana,
node_exporter): bump the version and its sha256 values together in your
host file (`iron_proxy_version` / `iron_proxy_sha256`, `monitoring_releases`)
or in the role defaults, taking the checksums from the project's published
checksum file.

**Claude Code and Codex** update themselves inside the agent zone; the
playbook only installs them once.

## Rotate the CA

The MITM CA lives 2 years (`iron_proxy_ca_days`); `DevboxCAExpiring` fires
30 days before expiry. To renew it, on the server:

```bash
sudo rm /etc/iron-proxy/ca/ca.crt /etc/iron-proxy/ca/ca.key
```

then run `site.yml`: a new CA is generated, iron-proxy restarts, and the
agent's CA bundle is rebuilt. Restart long-running agent tools so they
reload the bundle.

## Tighten the admin side

Not automated on purpose (an error would lock you out), but recommended once
the agents run in their zone:

- remove `NOPASSWD` from your own sudo rule (then run the playbook with `-K`);
- leave groups that are root-equivalent (`root`, `docker`) if you do not need them;
- make sure your home is not world-readable (the playbook warns if it is);
- rotate any secret that agents could read before you moved them;
- restrict inbound SSH to your VPN or tailnet in your provider's firewall.
