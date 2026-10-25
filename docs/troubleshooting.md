# Troubleshooting

## What does the error mean?

| The agent sees | Coming from | Meaning | Fix |
|---|---|---|---|
| HTTP `403` (or "CONNECT tunnel failed, response 403") | iron-proxy allowlist | host, method or path not allowed | allow it: [Operations](operations.md#allow-a-new-site) |
| HTTP `502` | iron-proxy deny list | the name resolves to a private, loopback or metadata address | if intended, `devbox_upstream_allow_cidrs` + an allow rule |
| `Connection refused` / reset, immediately | nftables (enforce) | non-HTTP traffic, a port other than 80/443, or another user's local service | `devbox_agent_direct_egress` or `devbox_agent_loopback_ports` |
| Clients outside the host cannot reach a server the agent runs | nftables (enforce) | inbound connections to the agent are refused | `devbox_agent_inbound_ports` |
| TLS error: unknown issuer / self-signed certificate | the tool does not trust the devbox CA | the tool ignores the standard CA variables | see [FAQ: TLS](faq.md#a-tool-fails-with-a-certificate-error) |
| `401` from the upstream API | the real token | secret not stored, expired, or lacking permissions | `sudo iron-proxy-set-secret <name>` |

## Finding the request

As the admin, on the server:

```bash
# the last refusals
sudo journalctl -u iron-proxy --since -1h | grep -E '"rejected_by"|"action":"warn"'
# firewall refusals (destination and port)
sudo journalctl -k --since -1h | grep -E '(agent|proxy)-egress:|agent-ingress:'
```

In Grafana, dashboard *agent-devbox / Egress*: *Recent requests outside the
allowlist* and *Firewall: connections outside the policy*.

The agent can look itself, through its read-only gateway:

```bash
curl -G http://127.0.0.1:9095/loki/api/v1/query_range \
  --data-urlencode 'query={unit="iron-proxy.service"} |= "rejected_by"'
```

## A tool ignores the proxy

Most tools read `HTTPS_PROXY`. Those that do not, and connect to :443 or
:80, are redirected transparently (IPv4) and work the same. Over IPv6 they
are refused in enforce mode; most fall back to IPv4. A tool that uses
another port needs a `devbox_agent_direct_egress` entry or, better, its
proxy setting.

## The agent's own server is refused (enforce)

The agent's processes must run in its slice: log in with `ssh agent@host`
(or `ssh agent@localhost`), not `sudo -u agent`. Services the agent starts
with `systemctl --user` are in the slice too.

## The playbook fails

| Message | Fix |
|---|---|
| `missing required arguments: devbox_admin_user` | set it in your host file |
| `Unsupported parameters` / `value of … must be one of` | a typo or a wrong type in your host file; see [configuration](configuration.md) |
| `Unknown setting(s), check the spelling` (warning) | a `devbox_*` name that does not exist |
| `… is owned by another user or contains files writable by group/others` | run from a checkout only you can modify (`chmod -R go-w`, or clone again with umask 022) |
| `has no ~/.ssh/authorized_keys: refusing to disable SSH password login` | add your key, or `devbox_harden_sshd: false` |
| `cgroup v2 (unified hierarchy) is required` | boot with the unified hierarchy (default on current distributions) |
| `devbox_upstream_allow_cidrs: … overlaps … which must stay denied` | loopback, link-local and metadata ranges cannot be allowed |
| `Reload iron-proxy` handler failed | the generated config was refused; the running one is kept. `sudo journalctl -u iron-proxy -n 50` |
| `--check` fails on a new server (`getent`, missing files) | `--check` needs a first real run; use it on a converged server |
| `python3-apt must be installed to use check mode`, or `Failed to set permissions on the temporary files … becoming an unprivileged user` | `--check` on a server never converged: install `python3-apt` and `acl` first (`sudo apt-get install python3-apt acl`; `dnf install acl` on RHEL). A normal run installs them by itself |
| `couldn't resolve module/action 'ansible.posix…'` | collections missing for the user running the playbook: run it without `sudo`, after `ansible-galaxy collection install -r requirements.yml` |

## The firewall table is missing

`DevboxEgressPolicyMissing` fires, or `sudo nft list table inet agent_egress`
fails: something flushed the ruleset (`systemctl reload nftables`,
`firewall-cmd --reload`, `ufw reload`…). Re-apply it with
`sudo systemctl restart agent-egress-firewall`; restarts of nftables.service
or firewalld re-apply it automatically, reloads do not.
