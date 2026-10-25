"""Unit tests for the filter and test plugins:  python3 -m pytest tests/unit"""
import importlib.util
import ipaddress
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _load(name, subdir):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, subdir, "devbox.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


devbox = _load("devbox_filters", "filter_plugins")
devbox_tests = _load("devbox_tests", "test_plugins")


def base(**over):
    p = {
        "mode": "enforce",
        "presets": {
            "npm": [{"host": "registry.npmjs.org", "methods": ["GET", "HEAD"]}],
            "llm": [{"host": "api.example.com"}],
        },
        "selected_presets": ["npm", "llm"],
        "rules_extra": [],
        "github_write_repos": [],
        "secrets": [],
        "secrets_dir": "/etc/iron-proxy/secrets",
        "deny_cidrs": ["127.0.0.0/8", "100.64.0.0/10"],
        "allow_cidrs": [],
        "ports": {"tunnel": 3128, "https": 18443, "http": 18081, "metrics": 9464, "management": 9092},
        "log_level": "info",
        "ca_cert": "/ca.crt",
        "ca_key": "/ca.key",
    }
    p.update(over)
    return p


def rules(cfg):
    return cfg["transforms"][0]["config"]["rules"]


def test_ip_network():
    assert devbox.ip_network("203.0.113.77", 24) == "203.0.113.0/24"
    assert devbox.ip_network("2001:db8::1", 64) == "2001:db8::/64"


def test_cidr_exclude_carves_single_host():
    out = devbox.cidr_exclude(["100.64.0.0/10"], ["100.101.1.2/32"])
    nets = [ipaddress.ip_network(n) for n in out]
    target = ipaddress.ip_address("100.101.1.2")
    assert not any(target in n for n in nets)
    # every other address of the range stays denied
    assert sum(n.num_addresses for n in nets) == 2 ** 22 - 1


def test_cidr_exclude_keeps_unrelated_and_other_family():
    assert devbox.cidr_exclude(["10.0.0.0/8", "fc00::/7"], ["100.101.1.2/32"]) == ["10.0.0.0/8", "fc00::/7"]


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "127.0.0.1/32", "169.254.169.254/32", "::1/128", "10.0.0.0/7", "not-an-ip"])
def test_allow_cidrs_refuses_dangerous_or_broad(bad):
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.cidr_exclude(["10.0.0.0/8"], [bad])


def test_restricted_rules_get_a_connect_tunnel_once():
    cfg = devbox.iron_proxy_config(base(rules_extra=[{"host": "registry.npmjs.org", "paths": ["/x"]}]))
    tunnels = [r for r in rules(cfg) if r.get("methods") == ["CONNECT"]]
    assert tunnels == [{"host": "registry.npmjs.org", "methods": ["CONNECT"]}]
    # unrestricted rules already match CONNECT
    assert not any(r.get("host") == "api.example.com" and "methods" in r for r in rules(cfg))


def test_observe_mode_sets_warn():
    assert devbox.iron_proxy_config(base(mode="observe"))["transforms"][0]["config"]["warn"] is True
    assert devbox.iron_proxy_config(base())["transforms"][0]["config"]["warn"] is False


def test_github_write_repos():
    cfg = devbox.iron_proxy_config(base(github_write_repos=["acme/app"]))
    push = [r for r in rules(cfg) if r.get("host") == "github.com" and r.get("methods") == ["POST"]]
    assert push[0]["paths"] == ["/acme/app.git/git-receive-pack", "/acme/app/git-receive-pack"]
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.iron_proxy_config(base(github_write_repos=["acme"]))


def test_secrets_are_rendered_and_validated():
    s = {"name": "acme_key", "proxy_value": "placeholder-1", "match_headers": ["X-Api-Key"],
         "match_path": True, "hosts": ["api.acme.example"], "allow": True}
    cfg = devbox.iron_proxy_config(base(secrets=[s]))
    sec = cfg["transforms"][1]["config"]["secrets"][0]
    assert sec["source"] == {"type": "file", "path": "/etc/iron-proxy/secrets/acme_key"}
    assert sec["replace"] == {"proxy_value": "placeholder-1", "match_headers": ["X-Api-Key"], "match_path": True}
    assert {"host": "api.acme.example"} in rules(cfg)
    for bad in ({**s, "name": "Bad-Name"}, {**s, "proxy_value": "short"}, {**s, "hosts": []},
                {**s, "match_header": ["typo"]}):
        with pytest.raises(devbox.AnsibleFilterError):
            devbox.iron_proxy_config(base(secrets=[bad]))
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.iron_proxy_config(base(secrets=[s, s]))


def test_no_secrets_transform_when_none():
    assert [t["name"] for t in devbox.iron_proxy_config(base())["transforms"]] == ["allowlist"]


def test_unknown_preset_and_bad_rule():
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.iron_proxy_config(base(selected_presets=["nope"]))
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.iron_proxy_config(base(rules_extra=[{"methods": ["GET"]}]))
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.iron_proxy_config(base(rules_extra=[{"host": "x", "method": ["GET"]}]))


def test_listeners_are_loopback_only():
    cfg = devbox.iron_proxy_config(base())
    for key in ("tunnel_listen", "https_listen", "http_listen"):
        assert cfg["proxy"][key].startswith("127.0.0.1:")
    assert cfg["metrics"]["listen"].startswith("127.0.0.1:")
    assert cfg["management"]["listen"].startswith("127.0.0.1:")


def test_host_networks():
    facts = {
        "interfaces": ["lo", "eth0", "docker0"],
        "lo": {"type": "loopback", "ipv4": {"address": "127.0.0.1", "prefix": "8"}},
        "eth0": {"ipv4": {"address": "203.0.113.77", "prefix": "24"},
                 "ipv6": [{"address": "2001:db8::77", "prefix": "64", "scope": "global"},
                          {"address": "fe80::1", "prefix": "64", "scope": "link"}]},
        "docker0": {"ipv4": {"address": "172.17.0.1", "prefix": "16"}},
    }
    assert devbox.host_networks(facts) == ["172.17.0.0/16", "2001:db8::/64", "203.0.113.0/24"]


def test_named_items():
    assert devbox.named_items(["a"], {"a": {"x": 1}, "b": {}}) == [{"x": 1, "name": "a"}]


def test_safe_env_value():
    assert devbox_tests.safe_env_value("https://example.com:8080/x,y")
    for bad in ("a b", 'a"b', "a'b", "a$b", "a`b", "a\\b", "a\nb", "a&b", "a;b", "a|b", "a(b)", "a<b", "a>b", "a*b"):
        assert not devbox_tests.safe_env_value(bad), bad


def test_direct_egress_validation():
    presets = {"tunnel": {"name": "t", "proto": "both", "ports": [7844]}}
    assert devbox.direct_egress([{"preset": "tunnel"}], presets)[0]["name"] == "t"
    for bad in ({"name": "x", "proto": "icmp", "ports": [1]}, {"name": "x", "proto": "tcp", "ports": []},
                {"name": "x", "proto": "tcp", "ports": ["70000"]}, {"name": "x", "proto": "tcp", "ports": [1], "cidrs": ["nope"]},
                {"preset": "missing"}):
        with pytest.raises(devbox.AnsibleFilterError):
            devbox.direct_egress([bad], presets)


def test_strip_scope_and_cidr_rules_get_tunnels():
    assert devbox.strip_scope("fe80::1%eth0") == "fe80::1"
    cfg = devbox.iron_proxy_config(base(rules_extra=[{"cidr": "203.0.113.5/32", "methods": ["GET"]}]))
    assert {"cidr": "203.0.113.5/32", "methods": ["CONNECT"]} in rules(cfg)


def test_allow_covering_whole_deny_range_and_require_refused():
    assert devbox.cidr_exclude(["10.1.0.0/16"], ["10.0.0.0/8"]) == []
    with pytest.raises(devbox.AnsibleFilterError):
        devbox.iron_proxy_config(base(secrets=[{"name": "k", "proxy_value": "placeholder", "hosts": ["h"], "require": True}]))
