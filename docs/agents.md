# Agents

What each agent needs, and what stays exposed in the agent zone. Snippets go
in your host file; secret values are stored on the server with
`sudo iron-proxy-set-secret <name>`.

| Agent | Install with | Credentials | Swapped by iron-proxy? |
|---|---|---|---|
| [Claude Code](#claude-code) | `devbox_agent_clis: [claude]` | subscription token (`claude setup-token`) | ✅ |
| [Codex](#codex) | `devbox_agent_clis: [codex]` | ChatGPT login | ❌ refreshing OAuth tokens stay in the agent zone |
| [T3 Code](#t3-code) | from the T3 Code app, over SSH | uses Claude Code / Codex | as above |
| [Hermes Agent](#hermes-agent) | `devbox_agent_clis: [hermes]` | provider API keys, bot tokens | ✅ API keys, Telegram, Slack; ❌ Discord, email, Nous Portal |

## Claude Code

Default setup: preset `anthropic`, built-in secret `claude_oauth`.

```yaml
devbox_agent_clis: [claude]
devbox_builtin_secrets: [claude_oauth, github_pat]
```

Generate the token with `claude setup-token` wherever you have Claude Code,
then store it as `claude_oauth`. The agent's `CLAUDE_CODE_OAUTH_TOKEN` is a
placeholder. Telemetry endpoints are not allowlisted.

## Codex

Preset `openai_codex` (enabled by default). Log in once as the agent:
`codex login --device-auth`. Codex refreshes its ChatGPT tokens itself, so
they cannot be swapped and live in the agent's `~/.codex/auth.json`: the
allowlist limits where they can be sent, and logging out revokes them.

## T3 Code

T3 Code drives Claude Code and Codex from its desktop or
mobile app over SSH. Connect it as the agent user (add the app's key to
`devbox_agent_ssh_keys`). For T3 Connect (remote access through T3's relay)
add:

```yaml
devbox_allow_presets: [anthropic, openai_codex, t3_code, github_read, npm, node, pypi]
devbox_agent_direct_egress:
  - preset: cloudflare_tunnel    # cloudflared, started by T3
```

The tunnel token is in the agent zone (T3 starts cloudflared as the agent).

## Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) reads provider
keys from its environment and `~/.hermes/.env`: give it placeholders and let
iron-proxy hold the real keys.

```yaml
devbox_agent_clis: [hermes]
devbox_allow_presets: [hermes, openrouter, telegram, github_read, npm, node, pypi]

devbox_secrets:
  - name: openrouter_api_key
    proxy_value: "sk-or-v1-ironproxy-placeholder-00000000000000000000000000000000"
    match_headers: ["Authorization"]
    hosts: ["openrouter.ai"]
  - name: telegram_bot_token          # the token is part of the URL path
    proxy_value: "1234567890:ironproxy-placeholder-000000000000000"
    match_path: true
    hosts: ["api.telegram.org"]

devbox_agent_env_extra:
  OPENROUTER_API_KEY: "sk-or-v1-ironproxy-placeholder-00000000000000000000000000000000"
  TELEGRAM_BOT_TOKEN: "1234567890:ironproxy-placeholder-000000000000000"
```

Then, on the server, `sudo iron-proxy-set-secret openrouter_api_key` and
`sudo iron-proxy-set-secret telegram_bot_token`. As the agent, configure the
rest with `hermes setup` and start the messaging gateway as a user service
(it runs in the agent's slice, with the agent's environment, and survives
logout thanks to linger).

The same pattern works for the other providers: enable `openai_api`,
`gemini`, `groq` or `mistral` (or add a rule for another host), declare the
key with the header the provider uses, and put the placeholder in the
matching variable (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`…).

| Hermes feature | Status |
|---|---|
| API-key providers (OpenRouter, OpenAI, Gemini, Groq, Mistral…) | ✅ key swapped |
| Telegram gateway | ✅ token swapped in the URL path (`match_path`) |
| Slack gateway (Socket Mode) | ✅ preset `slack`; `xoxb-`/`xapp-` tokens swapped on `Authorization` |
| Discord gateway | ⚠️ preset `discord`, but the bot token travels inside the WebSocket session, which iron-proxy does not rewrite: it stays in the agent zone |
| Email gateway (IMAP/SMTP) | ⚠️ not HTTP: a `devbox_agent_direct_egress` entry to your mail server, and the password stays in the agent zone |
| Nous Portal (`hermes setup --portal`) | ⚠️ preset `hermes`; OAuth tokens refresh themselves and stay in the agent zone |
| Web tools (Exa, Firecrawl, Parallel…) | ✅ allow the host and swap the key like a provider |
| Terminal backend `local`, `ssh`, `modal`, `daytona` | ✅ (`ssh` keys stay in the agent zone) |
| Terminal backend `docker` | ❌ the agent is never in the `docker` group (root-equivalent) |
| Telemetry (`telemetry.nousresearch.com`) | blocked in enforce mode (not allowlisted) |

**Hermes' own egress proxy (`hermes egress`)**: Hermes can start its own
iron-proxy for its Docker terminal backend only. It protects the sandbox,
while the main Hermes process (LLM calls, messaging gateway) keeps the real
keys in `~/.hermes/.env`, and the proxy's CA key lives under `~/.hermes/`.
On an agent-devbox server, leave it off: the whole Hermes process runs in
the agent zone with placeholders, its traffic is forced through the system
iron-proxy by nftables, and the CA key is out of its reach. The Docker
backend is unavailable to the agent anyway (the `docker` group is
root-equivalent).

The Hermes installer is unpinned and runs as the agent through the proxy; in
enforce mode it needs the `hermes`, `github_read`, `pypi` and `node` presets.
The system library it needs (`libatomic`) is installed by the playbook as
root beforehand, since the agent cannot run `apt-get`/`dnf` itself; later
needs go through `sudo agent-pkg-install`.
