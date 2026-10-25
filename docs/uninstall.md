# Uninstall

From your control machine:

```bash
ansible-playbook uninstall.yml                                       # keeps the agent account
ansible-playbook uninstall.yml -e devbox_uninstall_agent_user=true   # also removes it and its home
```

Add `-e devbox_uninstall_keep_cache=true` to keep the downloaded releases
(`/var/cache/agent-devbox`) for a later reinstall.

This stops and removes iron-proxy, the nftables table, the monitoring stack
(metrics and logs included), the agent helpers and their sudoers rule, the
sshd drop-ins (SSH password login is allowed again if your base
configuration allows it), the generated configuration and **the stored
secrets**.

It keeps the shared workspace (`devbox_workspace_root`) and the
`devbox_share_group` group, your admin account, and Tailscale if it was
installed. With `devbox_uninstall_agent_user=true`, the agent's home is
deleted: move anything you want to keep into the workspace first.

**Quick escape hatch** (on the server, without uninstalling): `sudo
systemctl stop agent-egress-firewall` removes the firewall table at once.
