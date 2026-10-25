# Architecture

## Zones

| Zone | Unix identity | Can | Cannot |
|---|---|---|---|
| admin | your login (`devbox_admin_user`) | everything, as before | nothing changes, except joining `devshare` |
| agent | `agent` (no password, groups: `devshare` only) | its home, the shared workspace, its own processes, the two sudo helpers | read the proxy's files, reach the network outside the policy, sudo |
| proxy | `iron-proxy` (system user, no login) | read the real secrets and the CA key, dial allowlisted hosts on 80/443 | anything else (hardened unit, read-only filesystem) |

The workspace (`devbox_workspace_root`, `/srv/work`) is setgid
`root:devshare`, and the agent works with `umask 002`: files created by
either side stay editable by both.

## A request, end to end

```
agent: curl https://registry.npmjs.org/left-pad
  │  HTTPS_PROXY=http://127.0.0.1:3128 (agent environment)
  ▼
iron-proxy tunnel listener (127.0.0.1:3128)
  1. CONNECT registry.npmjs.org:443 ─► allowlist: host allowed?     no ─► 403
  2. TLS terminated with a leaf certificate signed by the devbox CA
     (the agent trusts it through SSL_CERT_FILE, NODE_EXTRA_CA_CERTS…)
  3. GET /left-pad ─────────────────► allowlist: method/path allowed? no ─► 403
  4. secrets: placeholder found in a matched header/path? ─► real value swapped in
  5. DNS resolve ─► address in the deny list (loopback, private, metadata…)? ─► 502
  6. dial upstream on 80/443 as user iron-proxy (allowed by nftables)
```

Every request produces a JSON audit line in the iron-proxy journal (host,
method, path, verdict, which secret was swapped where; never secret values).

Tools that ignore `HTTPS_PROXY` and connect straight to an IP on :443 or :80
are redirected by nftables to iron-proxy's transparent listeners
(127.0.0.1:18443 and :18081), which read the TLS SNI or the Host header and
apply the same pipeline. Anything else is handled by the firewall below.

In **observe** mode, steps 1 and 3 let the request through and annotate the
audit entry (`request_transforms[0].annotations.action: "warn"`; the
top-level `action` stays `allow`); secrets are still swapped.

Allowlist and secret changes are applied with a reload, without dropping
connections; changes to listeners, the upstream deny list, TLS or logging
restart iron-proxy (the playbook decides which).


## Egress policy (nftables)

One table, `inet agent_egress`, loaded by `agent-egress-firewall.service`
before networking and before the agent's user manager starts. Other tables
(ufw, firewalld, Docker, Tailscale) are left alone; if `nftables.service` or
firewalld flushes the ruleset, the unit is restarted with them.

| Chain | Matches | Effect |
|---|---|---|
| `nat_out` | agent → any IPv4 :443/:80 | redirected to iron-proxy's transparent listeners |
| `agent_out` | packets sent by the agent | allowed: loopback (checked again on arrival), established flows, DNS to the host's resolvers (`devbox_agent_dns`), `devbox_agent_direct_egress`; anything else → `verdict_agent` |
| `proxy_out` | packets sent by iron-proxy | allowed: loopback, DNS, any host on 80/443, `devbox_proxy_direct_egress`; anything else → `verdict_proxy` |
| `agent_in` | new connections from outside the host to the agent's own sockets | allowed: `devbox_agent_inbound_ports`; anything else → `verdict_agent_in` (`agent-ingress:`) |
| `agent_lo` | new loopback connections from the agent | allowed: iron-proxy's ports, the gateway, `devbox_agent_loopback_ports`, and **any socket owned by the agent's own processes** (cgroup `user.slice/user-<uid>.slice`); anything else → `verdict_agent` |
| `verdict_*` | everything refused | logged (`agent-egress:` / `proxy-egress:`, rate-limited), then accepted (observe) or rejected (enforce) |
| `capture_*` | the agent's packets on `devbox_agent_capture_ports` | copied to NFLOG group 64 for `agent-capture` |

The loopback rule is what keeps the agent away from the admin's local
services: an IDE server, a database or an admin API listening on 127.0.0.1
belongs to another user's cgroup and is refused, while the agent's own test
servers, dev servers and T3 Code server are reachable on any port.

The agent's processes must run in its own slice for this to work: log in as
the agent (`ssh agent@host`, also from the server itself with
`ssh agent@localhost`), not with `sudo -u agent`, which stays in the admin's
session and would see its own servers refused in enforce mode.

## Secrets

| | Agent sees | iron-proxy swaps it on |
|---|---|---|
| `claude_oauth` | `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-ironproxy-placeholder-…` | `Authorization` to `api.anthropic.com` |
| `github_pat` | `GH_TOKEN` / `GITHUB_TOKEN=github_pat_ironproxy_placeholder_…`, git credential helper | `Authorization` (incl. Basic auth) to `github.com`, `api.github.com` |
| `devbox_secrets[]` | the `proxy_value` you put in your project's `.env` | the headers/path/query you declare, for the hosts you declare |

Values are stored in `/etc/iron-proxy/secrets/<name>` (`root:iron-proxy
0440`) by `iron-proxy-set-secret`, which only accepts declared names, checks
the format when a `pattern` is known, and reloads the proxy.

## Monitoring

```
journald ──► Alloy ──► Loki ◄── Grafana ◄── you (SSH tunnel / tailnet)
                         ▲          │
node_exporter ─► Prometheus ◄───────┘
                    ▲   ▲
                    │   └── devbox-obs-gateway (read-only) ◄── agent
                    └── your scrape targets
```

Alloy ships only what concerns the agent zone: the devbox units, the helper
commands, the agent's own processes and the egress-policy kernel lines. The
admin's other logs never leave the journal, because the agent can read Loki
through the gateway, which forwards query endpoints only (no push, delete or
admin API).

A timer exports devbox state for Prometheus: whether the nftables table is
loaded, CA expiry, which secrets are stored and their age, the current mode.
Alerts are evaluated in Prometheus and shown in Grafana; no notification
channel is configured.

## Installed files and services

| Path | What |
|---|---|
| `/usr/local/bin/iron-proxy` | pinned iron-proxy binary |
| `/etc/iron-proxy/proxy.yaml` | generated proxy configuration |
| `/etc/iron-proxy/startup-settings.yaml` | the part of it that needs a restart (change detection) |
| `/etc/iron-proxy/secrets/` | real secret values |
| `/etc/iron-proxy/ca/` | MITM CA key and certificate |
| `/etc/iron-proxy/management.env` | management API token (root only) |
| `/usr/local/share/iron-proxy/` | public CA, agent CA bundle, agent environment, agent git config |
| `/etc/agent-devbox/agent-egress.nft` | generated nftables policy |
| `/etc/agent-devbox/monitoring/` | Prometheus, Loki, Alloy, Grafana configuration and dashboards |
| `/etc/agent-devbox/grafana.env` | Grafana admin password (root only) |
| `/opt/agent-devbox/` | pinned monitoring binaries |
| `/usr/local/sbin/iron-proxy-set-secret`, `iron-proxy-reload` | admin commands |
| `/usr/local/sbin/agent-pkg-install`, `agent-capture` | agent helpers (sudo) |
| `/etc/sudoers.d/agent-devbox` | the agent's only sudo rule |
| `/etc/ssh/sshd_config.d/10-devbox-hardening.conf` | keys only, no root login |
| `/etc/ssh/sshd_config.d/90-agent-devbox.conf` | agent sessions: environment, no agent/X11 forwarding |
| `/var/lib/agent-pkg/` | the package helper's own sources and cache |
| `/var/lib/agent-captures/` | captures, owned by the agent |
| `/var/lib/agent-devbox/textfile/` | devbox metrics for node_exporter |
| `/etc/systemd/system/user@<uid>.service.d/agent-egress.conf` | re-applies the policy when the agent's user manager starts |
| `/var/lib/systemd/linger/<agent>` | keeps the agent's user services running without a login |
| `/var/cache/agent-devbox/` | downloaded releases |

| Unit | Listens on (127.0.0.1) |
|---|---|
| `iron-proxy.service` | 3128 (explicit proxy), 18443, 18081 (transparent), 9092 (management), 9464 (health) |
| `agent-egress-firewall.service` | – |
| `iron-proxy-agent-bundle.path` | – (rebuilds the agent CA bundle when system roots change) |
| `devbox-prometheus.service` | 9090 |
| `devbox-loki.service` | 3100, 9096 |
| `devbox-alloy.service` | 12345 |
| `devbox-grafana.service` | 3000 |
| `devbox-node-exporter.service` | 9100 |
| `devbox-obs-gateway.service` | 9095 (the agent's read-only gateway) |
| `devbox-textfile.timer` | – |
