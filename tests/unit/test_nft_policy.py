"""Render the nftables policy template and check its structure.

The policy is the second security-critical artifact (with the iron-proxy
config): these tests pin what each mode allows and refuses. If nft is
available and usable (root), the rendered policy is also syntax-checked.
"""
import importlib.util
import os
import shutil
import subprocess

import jinja2
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TEMPLATE = os.path.join(ROOT, "roles", "egress_firewall", "templates", "agent-egress.nft.j2")


def _load(name, subdir):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, subdir, "devbox.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


filters = _load("devbox_filters_nft", "filter_plugins").FilterModule().filters()
tests = _load("devbox_tests_nft", "test_plugins").TestModule().tests()


def render(**over):
    ctx = {
        "devbox_mode": "enforce",
        "devbox_agent_user": "agent",
        "devbox_proxy_user": "iron-proxy",
        "devbox_proxy_https_port": 18443,
        "devbox_proxy_http_port": 18081,
        "devbox_agent_dns": True,
        "devbox_agent_inbound_ports": [],
        "devbox_agent_capture_ports": [],
        "devbox_proxy_direct_egress": [],
        "__devbox_agent_uid": "1001",
        "__devbox_agent_mark": "0x01000000",
        "__devbox_capture_group": 64,
        "__egress_firewall_lo_ports": [3128, 18443, 18081],
        "__egress_firewall_dns_remote": ["192.0.2.53", "2001:db8::53"],
        "__egress_firewall_dns_loopback": [],
        "__egress_firewall_direct": [],
    }
    ctx.update(over)
    env = jinja2.Environment(trim_blocks=True, undefined=jinja2.StrictUndefined)
    env.filters.update(filters)
    # Ansible filters used by the template, not in plain Jinja.
    env.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
    env.tests.update(tests)
    with open(TEMPLATE) as fh:
        return env.from_string(fh.read()).render(**ctx)


def chain(text, name):
    start = text.index(f"  chain {name} {{")
    return text[start:text.index("\n  }", start)]


def test_enforce_rejects_and_observe_accepts():
    enforce, observe = render(), render(devbox_mode="observe")
    for name in ("verdict_agent", "verdict_proxy", "verdict_agent_in"):
        assert "reject with tcp reset" in chain(enforce, name)
        assert "accept" not in chain(enforce, name)
        assert "reject" not in chain(observe, name)
        assert chain(observe, name).rstrip().endswith("accept")


def test_agent_out_has_no_blanket_accept():
    out = chain(render(), "agent_out")
    for line in out.splitlines():
        line = line.strip()
        if line.endswith("accept"):
            assert any(k in line for k in ("oif \"lo\"", "127.0.0.0/8", "::1", "established", "dport")), line


def test_dns_toggle():
    assert "@dns4" in chain(render(), "agent_out")
    assert "@dns4" not in chain(render(devbox_agent_dns=False), "agent_out")
    # iron-proxy always needs DNS
    assert "@dns4" in chain(render(devbox_agent_dns=False), "proxy_out")


def test_direct_egress_split_by_family():
    direct = filters["devbox_direct_egress"](
        [{"name": "db", "proto": "tcp", "ports": [5432], "cidrs": ["198.51.100.7/32", "2001:db8::7/128"]}], {})
    out = chain(render(__egress_firewall_direct=direct), "agent_out")
    assert "ip daddr { 198.51.100.7/32 } tcp dport { 5432 } accept" in out
    assert "ip6 daddr { 2001:db8::7/128 } tcp dport { 5432 } accept" in out


def test_inbound_ports_and_capture_chains():
    text = render(devbox_agent_inbound_ports=["4001-4002"], devbox_agent_capture_ports=[5432])
    assert "th dport { 4001-4002 } accept" in chain(text, "agent_in")
    assert "log group 64" in chain(text, "capture_out")
    assert "capture_out" not in render()


def test_loopback_policy_matches_agent_slice():
    lo = chain(render(), "agent_lo")
    assert 'socket cgroupv2 level 2 "user.slice/user-1001.slice" accept' in lo
    assert lo.rstrip().endswith("jump verdict_agent")


@pytest.mark.skipif(shutil.which("nft") is None or os.geteuid() != 0, reason="needs nft and root")
def test_nft_accepts_the_policy(tmp_path):
    path = tmp_path / "policy.nft"
    path.write_text(render().replace('"user.slice/user-1001.slice"', '"user.slice"'))
    subprocess.run(["nft", "-c", "-f", str(path)], check=True)
