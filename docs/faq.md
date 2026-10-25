# FAQ

### Do I need API keys?

No. Claude Code uses your subscription through a long-lived token
(`claude setup-token`) that iron-proxy swaps in; Codex keeps its ChatGPT
login in the agent zone (its tokens refresh themselves, so they cannot be
swapped). If you prefer API keys, drop `claude_oauth` from
`devbox_builtin_secrets` and declare the key as a custom secret:

```yaml
devbox_builtin_secrets: [github_pat]
devbox_secrets:
  - { name: anthropic_api_key, proxy_value: "sk-ant-api03-placeholder-0000", match_headers: ["X-Api-Key"], hosts: ["api.anthropic.com"] }
devbox_agent_env_extra: { ANTHROPIC_API_KEY: "sk-ant-api03-placeholder-0000" }
```

### A tool fails with a certificate error

All HTTPS from the agent is decrypted and re-encrypted by iron-proxy with
a certificate from the devbox CA. The agent environment points the usual
variables at it (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`,
`CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`, `CARGO_HTTP_CAINFO`, `PIP_CERT`,
`AWS_CA_BUNDLE`, `DENO_CERT`). Tools with their own trust store need one
more step, done as the agent:

| Tool | |
|---|---|
| Java (Maven, Gradle, Android) | import `/usr/local/share/iron-proxy/ca.crt` into a truststore and pass it with `JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=…` (via `devbox_agent_env_extra`) |
| Browsers / Playwright | add the CA to the browser profile (`certutil` for NSS) |
| Containers | images have their own CA store: copy the CA into the image or mount it |

### Can the agent use Docker or Podman?

Not Docker: membership in the `docker` group is root-equivalent, so the
agent is never added to it. Rootless Podman works as the agent (pulls go
through the proxy with the `container_registries` preset), but the
containers' own traffic is subject to the same policy, and the containers do
not trust the devbox CA unless you add it to the image.

### GitLab, Bitbucket or a self-hosted forge?

Allow the host and declare the token as a custom secret. For example, GitLab
over HTTPS:

```yaml
devbox_allow_rules_extra:
  - { host: "gitlab.com" }
devbox_secrets:
  - { name: gitlab_token, proxy_value: "glpat-placeholder-00000000000", match_headers: ["Authorization", "Private-Token"], hosts: ["gitlab.com"] }
```

and use the placeholder as the git password for `gitlab.com` in the agent's
git configuration. Pushes are then bounded by the token's own scope.

### Why can't the agent reach my local database?

New loopback connections from the agent only reach its own processes, the
proxy and the ports you list. Share a service explicitly with
`devbox_agent_loopback_ports: [5432]`, knowing it gives the agent whatever
access that service grants. Unix sockets are not filtered: rely on their
file permissions.

### Several agent users, or several projects with different policies?

One agent user per server today. Use one server (or VM) per trust level.

### Does it work on macOS or Windows?

As the control machine, yes (Windows through WSL). The server side needs
Linux: the confinement relies on nftables, cgroup v2 and systemd.

### What happens to my existing users and files?

Nothing, apart from your admin account joining `devshare` and SSH password
login being disabled for everyone (`devbox_harden_sshd`). If
`devbox_agent_user` names an existing account, its supplementary groups are
reset, its password is locked and its SSH keys are replaced: pick a new name.
