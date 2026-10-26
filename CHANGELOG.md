# Changelog

All notable changes to agent-devbox. Re-run `site.yml` after upgrading.

## 0.1.0 - 2026-10-26

First release.

- One Ansible playbook that splits a Linux dev server into three zones:
  your admin account (unchanged), a dedicated `agent` user for coding
  agents, and [iron-proxy](https://github.com/paradigmxyz/iron-proxy)
  holding the real secrets.
- Egress policy per user with nftables: the agent reaches the internet only
  through iron-proxy (allowlist by host, method and path), with declared
  exceptions for non-HTTP traffic. Loopback and inbound connections are
  restricted to the agent's own processes and declared ports.
- `observe` and `enforce` modes, to see what agents use before blocking.
- Placeholder tokens swapped at the boundary: Claude subscription token,
  GitHub fine-grained token, and any API key you declare.
- Allowlist presets for LLM providers, GitHub, package registries (npm,
  PyPI, crates, Go, Maven, Gradle, RubyGems, Packagist, NuGet, conda,
  Hugging Face, container registries), documentation sites, VS Code and
  Copilot, T3 Code, Hermes Agent, messaging platforms.
- Agent helpers: `agent-pkg-install` (official repositories only) and
  `agent-capture`.
- Agents: Claude Code, Codex and Hermes Agent installed for the agent user;
  T3 Code supported.
- Monitoring: Prometheus, Loki, Grafana, Alloy and node_exporter on
  localhost, with egress and host dashboards, alerts, and a read-only query
  gateway for the agent. Optional Tailscale, with Grafana on the tailnet.
- `verify.yml` to check the confinement as the agent, `uninstall.yml`, and
  a configuration schema validated at start.
- Tested on Debian 13, Ubuntu 24.04 and Rocky Linux 9.
