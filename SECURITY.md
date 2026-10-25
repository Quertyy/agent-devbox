# Security model

agent-devbox assumes the agent can be **fully compromised**: a prompt
injection, a malicious dependency or a `postinstall` script runs arbitrary
code as the agent user. The goal is that such code can neither read your
secrets nor send data anywhere you did not allow, and cannot become root.

## What is protected

| Asset | How |
|---|---|
| Admin credentials (SSH keys, cloud CLIs, `.env` files in your home) | Separate Unix user with no sudo and no membership in `root`/`adm`/`wheel`/`docker`. Your home stays as private as its permissions make it: the playbook warns if it is world-readable, it does not change it. |
| Tokens used by the agent (Claude subscription, GitHub PAT, service keys) | Only placeholders in the agent zone. iron-proxy (separate user, hardened unit) swaps the real value at egress, only for the declared hosts. A stolen placeholder is useless elsewhere. |
| Network egress | nftables per UID, default deny: the agent reaches the internet only through iron-proxy (allowlist by host, method and path) and the `devbox_agent_direct_egress` exceptions. Direct IPv4 :443/:80 is redirected into the proxy; everything else is rejected and logged. |
| Local services | New loopback TCP/UDP connections from the agent are accepted only towards its own processes (cgroup match), the proxy, its gateway and `devbox_agent_loopback_ports`. |
| Internal networks | iron-proxy refuses to dial loopback, RFC 1918, CGNAT/Tailscale, link-local/metadata and the host's own networks, even for an allowlisted name (DNS rebinding). Exceptions cannot cover loopback, link-local or metadata ranges. |
| Root | The agent's only sudo entries are `agent-pkg-install` (bare package names, official repositories, signatures enforced, no local files) and `agent-capture` (fixed source, duration and size). The playbook writes inside the agent's home as the agent, never as root. |
| Audit | Every proxied request is logged (host, method, path, verdict, secret swaps, never secret values); the egress policy logs refused connections. Logs go to Loki, which the agent can query but not write to through its gateway. |
| Firewall integrity | The policy is re-applied when `nftables.service` or firewalld restart; an alert fires within 15 minutes if the table goes missing (e.g. after `systemctl reload nftables`, which flushes the ruleset). |

## The shared workspace belongs to the agent

Everything under `devbox_workspace_root` can be modified by the agent: code,
`Makefile`s, `package.json` scripts, `.git/config` (`core.fsmonitor`),
`.git/hooks`, `.envrc`, `.vscode/tasks.json`… Any tool that **runs** something
from there runs it as whoever started the tool. If that is you, a hostile
agent gets your account, and through your sudo, the server.

- **Work on the code as the agent**: connect your IDE (VS Code / Cursor
  Remote-SSH, JetBrains Gateway) to `agent@host` rather than to your admin
  account. Enable the `vscode` preset so the IDE server can install and
  update itself through the proxy.
- **As the admin, only read** files there (`less`, `cat`); do not run git,
  build tools, test runners, or open the folder in an IDE, since IDEs run git
  and task providers automatically.
- Keep your own clones, the ones you push from with your own keys, outside
  the workspace.

## What is NOT protected

Know these when you decide what to put in the agent zone:

- **Data the agent can read can reach allowed destinations.** The LLM
  provider is always allowed, and so is everything in your allowlist. Do not
  keep in the agent zone data that must not reach your provider.
- **GitHub writes are bounded by the token, not only by the allowlist.**
  Pushes and REST writes are limited to `devbox_github_write_repos`, but
  GraphQL (used by `gh`) cannot be filtered by repository: limit the
  fine-grained token to the same repositories.
- **Direct egress bypasses iron-proxy.** A `devbox_agent_direct_egress` entry
  without `cidrs` ("any host on port X") is an exfiltration channel; so is
  direct DNS to the host's resolvers (`devbox_agent_dns`, on by default),
  whose queries are not audited.
- **Credentials that cannot be swapped live in the agent zone**: Codex's
  ChatGPT login, URLs whose hostname is the secret, keys used locally (SSH,
  GPG, signing). Treat them as exposed: least privilege, short lifetime,
  easy revocation.
- **The swapped tokens can still be used** by whoever controls the agent,
  through the proxy, for as long as they control it. Revoke them if the zone
  is compromised.
- **Daemons that act for the agent are outside its policy.** The egress
  policy matches the agent's own UID. A service the agent can talk to over a
  Unix socket or a local queue and that reaches the network under its own
  user (a mail transport agent, a print server, a database link…) is a way
  out. `agent-pkg-install` refuses mail transport agents, even as
  dependencies; review what else runs on the server. Unix sockets are not
  filtered by the loopback policy: only their file permissions apply. The
  agent is never added to the `docker` group (root-equivalent).
- **Name lookups through the local resolver are always possible.** With
  systemd-resolved or nscd, lookups go through a local socket and leave as
  the resolver's user, whatever `devbox_agent_dns` says: a lookup of
  `<data>.attacker.example` carries data out. `devbox_agent_dns: false` only
  stops DNS packets sent by the agent itself.
- **Inbound connections** to the agent's servers are refused unless their
  port is in `devbox_agent_inbound_ports`; an allowed port is a two-way
  channel the proxy does not see. Keep your provider firewall closed too.
- **Observe mode blocks nothing**, locally either: during the rollout the
  agent can reach other users' local services, including Loki's write API.
  It also under-reports IPv6: tools that ignore the proxy and connect over
  IPv6 are not redirected, so they appear in the firewall log, not in the
  proxy audit.
- **Reading the web is a way out too.** Every allowlisted host receives
  the URLs the agent requests. Multi-tenant documentation hosts in the
  `docs` preset (GitBook, Read the Docs, GitHub Pages) show visit analytics
  to page owners, so GET requests there can carry a little data out. Leave
  the preset off if that matters more than convenience.
- **Metrics you add to Prometheus are readable by the agent** through its
  query gateway (`devbox_monitoring_scrape_targets`, like the journal units
  you add to Loki).
- **Refused connections are logged at a limited rate** (20/s): a flood can
  hide individual attempts in the log, not let them through.
- **Distribution packages run maintainer scripts as root.** Only official
  repositories are used, but `agent-pkg-install` remains a trust decision:
  disable it with `devbox_agent_pkg_install: false` if that is too much.
- **Claude Code and Codex are installed unpinned** (official installer,
  latest release), as the agent and through the proxy; they then update
  themselves.
- **Local privilege escalation** (kernel, setuid bugs) is out of scope: keep
  the server patched.

## Running the playbook safely

The playbook runs as root on your servers with code from your checkout
(tasks, templates, Python plugins). Run it from a checkout only you can
modify, typically on your laptop. It refuses to run from a directory owned
by someone else or writable by group/others, such as a clone inside the
shared workspace.

## Pinning

iron-proxy, Prometheus, Loki, Alloy, Grafana and node_exporter are
downloaded from upstream releases and checked against sha256 values
committed in `roles/*/defaults/main.yml`. Upstream iron-proxy releases are
not signed: review a release before bumping its checksum.

## Reporting a vulnerability

Please report vulnerabilities privately through a GitHub security advisory
on this repository rather than a public issue.
