"""Filters used by agent-devbox templates (Python stdlib only).

The iron-proxy configuration is built here, as data, instead of in Jinja: it
is the security-critical part of the project and is covered by
tests/unit/test_filters.py.
"""
import ipaddress

try:
    from ansible.errors import AnsibleFilterError
except ImportError:  # unit tests without Ansible installed
    class AnsibleFilterError(Exception):
        pass


# Ranges that must stay denied for iron-proxy whatever the user configures:
# an exception here would let an allowlisted name reach the host itself or a
# cloud metadata endpoint.
NEVER_ALLOWED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "fe80::/10", "ff00::/8", "fd00:ec2::254/128", "fd20:ce::254/128",
)]
MIN_PREFIX = {4: 8, 6: 32}

SECRET_KEYS = {"name", "proxy_value", "hosts", "match_headers", "match_path",
               "match_query", "match_body", "allow", "pattern"}
RULE_KEYS = {"host", "cidr", "methods", "paths"}


def ip_network(address, prefix):
    """'203.0.113.77', 24 -> '203.0.113.0/24' (works for IPv4 and IPv6)."""
    return str(ipaddress.ip_interface(f"{address}/{prefix}").network)


def ip_version(address):
    """4 or 6 for an address or network."""
    return ipaddress.ip_network(str(address), strict=False).version


def check_allow_cidrs(allow):
    """Validate devbox_upstream_allow_cidrs; return the parsed networks."""
    nets = []
    for a in allow:
        try:
            net = ipaddress.ip_network(str(a), strict=True)
        except ValueError as e:
            raise AnsibleFilterError(f"devbox_upstream_allow_cidrs: {a!r} is not a network ({e})")
        if net.prefixlen < MIN_PREFIX[net.version]:
            raise AnsibleFilterError(
                f"devbox_upstream_allow_cidrs: {a} is too broad (minimum /{MIN_PREFIX[net.version]})")
        for f in NEVER_ALLOWED:
            if net.version == f.version and net.overlaps(f):
                raise AnsibleFilterError(
                    f"devbox_upstream_allow_cidrs: {a} overlaps {f}, which must stay denied")
        nets.append(net)
    return nets


def cidr_exclude(deny, allow):
    """Remove the networks in `allow` from the networks in `deny`.

    iron-proxy's upstream deny list has no exceptions, so an allowed address
    inside a denied range is carved out: ['100.64.0.0/10'] minus
    ['100.101.1.2/32'] gives the ranges around that single address.
    """
    allow_nets = check_allow_cidrs(allow)
    result = []
    for d in deny:
        pending = [ipaddress.ip_network(str(d), strict=False)]
        for a in allow_nets:
            nxt = []
            for net in pending:
                if net.version != a.version:
                    nxt.append(net)
                elif a.subnet_of(net):
                    nxt.extend(net.address_exclude(a))
                elif not net.subnet_of(a):
                    nxt.append(net)
            pending = nxt
        result.extend(str(n) for n in sorted(pending))
    seen = set()
    return [n for n in result if not (n in seen or seen.add(n))]


def _check_rule(rule, where):
    if not isinstance(rule, dict) or not ({"host", "cidr"} & rule.keys()):
        raise AnsibleFilterError(f"{where}: each rule needs 'host' or 'cidr': {rule!r}")
    unknown = set(rule) - RULE_KEYS
    if unknown:
        raise AnsibleFilterError(f"{where}: unknown key(s) {sorted(unknown)} in rule {rule!r}")
    if "cidr" in rule:
        ipaddress.ip_network(str(rule["cidr"]), strict=False)


def _check_secrets(secrets):
    names = set()
    for s in secrets:
        if "require" in s:
            # iron-proxy also evaluates the secrets transform on the CONNECT
            # that opens an explicit-proxy tunnel, which carries no
            # placeholder: "require" would refuse every HTTPS_PROXY client.
            raise AnsibleFilterError(f"secret {s.get('name', '?')}: 'require' is not supported (it would block "
                                     "every client using HTTPS_PROXY)")
        unknown = set(s) - SECRET_KEYS
        if unknown:
            raise AnsibleFilterError(f"secret {s.get('name', '?')}: unknown key(s) {sorted(unknown)}")
        name = s.get("name", "")
        if not name or not all(c.islower() or c.isdigit() or c == "_" for c in name) or len(name) > 64:
            raise AnsibleFilterError(f"secret {name!r}: name must match [a-z0-9_]{{1,64}}")
        if name in names:
            raise AnsibleFilterError(f"secret {name!r} is declared twice")
        names.add(name)
        if len(str(s.get("proxy_value", ""))) < 8:
            raise AnsibleFilterError(f"secret {name}: proxy_value must be at least 8 characters")
        if not s.get("hosts"):
            raise AnsibleFilterError(f"secret {name}: hosts must list at least one host")


def iron_proxy_config(p):
    """Build iron-proxy's YAML config (as a dict) from the devbox settings.

    `p` keys: mode, presets, selected_presets, rules_extra, github_write_repos,
    secrets, secrets_dir, deny_cidrs, allow_cidrs, ports, log_level, ca_cert,
    ca_key.
    """
    unknown = [x for x in p["selected_presets"] if x not in p["presets"]]
    if unknown:
        raise AnsibleFilterError(f"devbox_allow_presets: unknown preset(s) {unknown}; "
                                 f"available: {sorted(p['presets'])}")
    _check_secrets(p["secrets"])

    rules = []
    for name in p["selected_presets"]:
        rules.extend(p["presets"][name])

    repos = p["github_write_repos"]
    if repos:
        for r in repos:
            if r.count("/") != 1 or not all(r.split("/")):
                raise AnsibleFilterError(f"devbox_github_write_repos: {r!r} is not owner/repo")
        rules.append({"host": "github.com", "methods": ["POST"],
                      "paths": [p for r in repos for p in (f"/{r}.git/git-receive-pack", f"/{r}/git-receive-pack")]})
        rules.append({"host": "api.github.com", "methods": ["POST", "PATCH", "PUT", "DELETE"],
                      "paths": [f"/repos/{r}/*" for r in repos]})

    for s in p["secrets"]:
        if s.get("allow"):
            rules.extend({"host": h} for h in s["hosts"])

    for r in p["rules_extra"]:
        _check_rule(r, "devbox_allow_rules_extra")
    rules.extend(p["rules_extra"])

    # Explicit-proxy clients open a CONNECT tunnel first, which the allowlist
    # sees as method CONNECT with an empty path. Allow the tunnel for every
    # host or CIDR that has method/path-restricted rules: iron-proxy only
    # accepts TLS (always MITM'd) or plain HTTP inside, and evaluates every
    # inner request against these same rules.
    for r in list(rules):
        if "methods" in r or "paths" in r:
            key = "host" if "host" in r else "cidr"
            tunnel = {key: r[key], "methods": ["CONNECT"]}
            if tunnel not in rules:
                rules.append(tunnel)

    transforms = [{"name": "allowlist", "config": {"warn": p["mode"] == "observe", "rules": rules}}]
    secrets = []
    for s in p["secrets"]:
        replace = {"proxy_value": s["proxy_value"], "match_headers": list(s.get("match_headers", []))}
        for opt in ("match_path", "match_query", "match_body"):
            if opt in s:
                replace[opt] = bool(s[opt])
        secrets.append({
            "source": {"type": "file", "path": f"{p['secrets_dir']}/{s['name']}"},
            "replace": replace,
            "rules": [{"host": h} for h in s["hosts"]],
        })
    if secrets:
        transforms.append({"name": "secrets", "config": {"secrets": secrets}})

    ports = p["ports"]
    return {
        "dns": {"enabled": False},
        "proxy": {
            "tunnel_listen": f"127.0.0.1:{ports['tunnel']}",
            "https_listen": f"127.0.0.1:{ports['https']}",
            "http_listen": f"127.0.0.1:{ports['http']}",
            "upstream_deny_cidrs": cidr_exclude(p["deny_cidrs"], p["allow_cidrs"]),
        },
        "metrics": {"listen": f"127.0.0.1:{ports['metrics']}"},
        "management": {"listen": f"127.0.0.1:{ports['management']}", "api_key_env": "IRON_MANAGEMENT_API_KEY"},
        "tls": {"ca_cert": p["ca_cert"], "ca_key": p["ca_key"]},
        "transforms": transforms,
        "log": {"level": p["log_level"]},
    }


def host_networks(facts):
    """Networks of every non-loopback interface (from gathered facts).

    iron-proxy must never dial the host's own networks: an allowlisted name
    resolving to a neighbour or to the host itself would otherwise reach it.
    """
    nets = set()
    for iface in facts.get("interfaces", []):
        info = facts.get(iface.replace("-", "_").replace(".", "_").replace(":", "_"), {})
        if not isinstance(info, dict) or info.get("type") == "loopback":
            continue
        v4 = info.get("ipv4")
        for a in ([v4] if isinstance(v4, dict) else []) + list(info.get("ipv4_secondaries", [])):
            if a.get("address") and a.get("prefix"):
                nets.add(ip_network(a["address"], a["prefix"]))
        for a in info.get("ipv6", []):
            if a.get("scope") == "global" and a.get("address") and a.get("prefix"):
                nets.add(ip_network(a["address"], a["prefix"]))
    return sorted(nets)


def direct_egress(entries, presets):
    """Resolve and validate devbox_agent_direct_egress (presets expanded)."""
    out = []
    for e in entries:
        if "preset" in e:
            if e["preset"] not in presets:
                raise AnsibleFilterError(f"devbox_agent_direct_egress: unknown preset {e['preset']!r}")
            e = presets[e["preset"]]
        name = e.get("name") or "?"
        if e.get("proto") not in ("tcp", "udp", "both"):
            raise AnsibleFilterError(f"devbox_agent_direct_egress {name}: proto must be tcp, udp or both")
        if not e.get("ports"):
            raise AnsibleFilterError(f"devbox_agent_direct_egress {name}: ports is required")
        for port in e["ports"]:
            lo, _, hi = str(port).partition("-")
            if not (lo.isdigit() and (not hi or hi.isdigit()) and 0 < int(lo) <= int(hi or lo) < 65536):
                raise AnsibleFilterError(f"devbox_agent_direct_egress {name}: bad port {port!r}")
        for c in e.get("cidrs", []):
            try:
                ipaddress.ip_network(str(c), strict=False)
            except ValueError:
                raise AnsibleFilterError(f"devbox_agent_direct_egress {name}: {c!r} is not a network")
        out.append(dict(e, name=name))
    return out


def strip_scope(address):
    """'fe80::1%eth0' -> 'fe80::1' (resolv.conf zone ids are not valid in nft sets)."""
    return str(address).split("%", 1)[0]


def named_items(names, defs):
    """['a'], {'a': {...}} -> [{'name': 'a', ...}] (built-in secret definitions)."""
    return [dict(defs[n], name=n) for n in names]


class FilterModule:
    def filters(self):
        return {
            "devbox_ip_network": ip_network,
            "devbox_ip_version": ip_version,
            "devbox_cidr_exclude": cidr_exclude,
            "devbox_iron_proxy_config": iron_proxy_config,
            "devbox_named_items": named_items,
            "devbox_direct_egress": direct_egress,
            "devbox_strip_scope": strip_scope,
            "devbox_host_networks": host_networks,
        }
