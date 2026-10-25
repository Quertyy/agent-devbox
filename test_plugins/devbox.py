"""Jinja tests for templates, stdlib only."""
import ipaddress


def ip_version_is(address, version):
    """select('devbox_ip_version_is', 4) keeps IPv4 addresses/networks."""
    return ipaddress.ip_network(str(address), strict=False).version == int(version)


def ip_is_loopback(address):
    return ipaddress.ip_network(str(address), strict=False).is_loopback


SAFE_ENV_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/@%+=,-")


def safe_env_value(value):
    """True if a value can be written unquoted to sshd SetEnv, systemd
    EnvironmentFile/environment.d and a sourced shell file alike (allowlist:
    letters, digits and ._:/@%+=,-)."""
    return set(str(value)) <= SAFE_ENV_CHARS


class TestModule:
    def tests(self):
        return {
            "devbox_ip_version_is": ip_version_is,
            "devbox_ip_loopback": ip_is_loopback,
            "devbox_safe_env_value": safe_env_value,
        }
