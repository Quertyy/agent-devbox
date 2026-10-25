#!/bin/bash
# Integration test: for each distribution, converge a fresh systemd container
# in observe mode and verify, switch to enforce and verify again, then check
# that a second run and a --check run change nothing, then uninstall.
#
#   tests/run.sh [debian|ubuntu|rocky]...   (default: all)
#
# Environment:
#   KEEP=1               keep the containers afterwards
#   TEST_EXTRA_CA=<pem>  CA of an intercepting proxy in front of this machine
#   TEST_PODMAN_OPTS     extra "podman run" options (CI: --privileged, for
#                        hosts whose AppArmor/seccomp defaults stop systemd)
# Runs with rootless podman; on hosts without a systemd user session (CI
# runners), run it as root instead: sudo -E env "PATH=$PATH" tests/run.sh
#
# Needs podman, ansible-core, requirements.yml and tests/requirements.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

declare -A BASE=(
  [debian]=docker.io/library/debian:trixie
  [ubuntu]=docker.io/library/ubuntu:24.04
  [rocky]=quay.io/rockylinux/rockylinux:9
)
read -ra distros <<< "${*:-debian ubuntu rocky}"
for d in "${distros[@]}"; do
  [[ -n ${BASE[$d]:-} ]] || { echo "unknown distribution: $d (known: ${!BASE[*]})" >&2; exit 2; }
done

# The test targets are throwaway containers: running from this (possibly
# shared) checkout is fine here, see tasks/check-checkout.yml.
export DEVBOX_ALLOW_UNSAFE_CHECKOUT=1
export ANSIBLE_FORCE_COLOR=0
cache=${XDG_CACHE_HOME:-$HOME/.cache}/agent-devbox-test
mkdir -p "$cache/downloads"
ctx=$(mktemp -d)
trap 'rm -rf "$ctx"' EXIT
cp tests/Containerfile "$ctx/"
: > "$ctx/extra-ca.pem"
[[ -n ${TEST_EXTRA_CA:-} ]] && cp "$TEST_EXTRA_CA" "$ctx/extra-ca.pem"

run() { # host, playbook args...
  local host=$1; shift
  ansible-playbook -i tests/inventory.yml -l "$host" "$@" || { dump "$host"; return 1; }
}

dump() { # container: state useful to debug a failed run (no secrets)
  echo "--- diagnostics for $1"
  podman exec "$1" bash -c '
    journalctl -n 40 --no-pager -o short 2>/dev/null | grep -Ei "pam|sudo|denied|fail" | tail -20
    ls -la /etc/shadow /etc/passwd
    awk -F: "{print \$1\":\"(length(\$2) > 3 ? \"<hash>\" : \$2)\":\"\$3\":\"\$8}" /etc/shadow | tail -5
    sudo -n -u agent id 2>&1 | tail -2' 2>&1 || true
}

expect_no_change() { # label, output
  if ! grep -qE "^$name +: ok=[0-9]+ +changed=0 +unreachable=0 +failed=0" <<< "$2"; then
    tail -20 <<< "$2"
    echo "FAIL $d: $1 reported changes or failures"
    exit 1
  fi
}

echo "=== checkout check refuses a group-writable checkout"
unsafe=$(mktemp -d)
cp -r . "$unsafe/repo"
chmod -R go-w "$unsafe/repo"
chmod g+w "$unsafe/repo/site.yml"
printf 'devbox:\n  hosts:\n    localhost: { ansible_connection: local }\n' > "$unsafe/inventory.yml"
# The check runs before facts are gathered: nothing touches the host.
out=$(cd "$unsafe/repo" && DEVBOX_ALLOW_UNSAFE_CHECKOUT=0 ansible-playbook -i "$unsafe/inventory.yml" site.yml 2>&1) || true
rm -rf "$unsafe"
[[ $out == *"contains files owned by another user or writable by"* ]] ||
  { echo "FAIL: the checkout check did not refuse a group-writable file"; echo "$out" | tail -5; exit 1; }

for d in "${distros[@]}"; do
  name=devbox-test-$d
  echo "=== $d: build and start"
  podman build --http-proxy=false -q -t "localhost/$name" --build-arg "BASE=${BASE[$d]}" "$ctx" >/dev/null
  podman rm -f "$name" >/dev/null 2>&1 || true
  read -ra extra_opts <<< "${TEST_PODMAN_OPTS:-}"
  podman run -d --http-proxy=false --name "$name" --hostname "$name" --systemd=always \
    --cap-add NET_ADMIN --cap-add NET_RAW --cap-add SYS_ADMIN --ulimit core=-1 "${extra_opts[@]}" \
    -v "$cache/downloads:/var/cache/agent-devbox:Z" \
    "localhost/$name" >/dev/null
  state=
  for _ in $(seq 90); do
    state=$(podman exec "$name" systemctl is-system-running 2>/dev/null || true)
    [[ $state == running || $state == degraded ]] && break
    sleep 1
  done
  if [[ $state != running && $state != degraded ]]; then
    echo "FAIL $d: systemd did not start (${state:-container not running})"
    podman ps -a --filter "name=$name" --format '{{.Names}} {{.Status}}' || true
    podman logs --tail 40 "$name" 2>&1 || true
    podman exec "$name" systemctl --failed --no-pager 2>&1 | tail -20 || true
    exit 1
  fi

  echo "=== $d: converge (observe)"
  run "$name" site.yml -e devbox_mode=observe
  echo "=== $d: verify (observe)"
  run "$name" verify.yml -e devbox_mode=observe
  echo "=== $d: converge (enforce)"
  run "$name" site.yml -e devbox_mode=enforce
  echo "=== $d: verify (enforce)"
  run "$name" verify.yml -e devbox_mode=enforce
  echo "=== $d: secret swap and deny list"
  podman exec -i "$name" iron-proxy-set-secret demo_token <<< "demo-realvalue0001" >/dev/null
  as_agent() {
    podman exec "$name" bash -c "systemd-run --quiet --wait --pipe --uid=agent -p Slice=user-\$(id -u agent).slice \
      -p EnvironmentFile=/usr/local/share/iron-proxy/agent-env $1"
  }
  swapped=$(as_agent "curl -s -m 20 -H 'X-Demo-Token: demo-placeholder-0001' https://httpbin.org/headers")
  [[ $swapped == *demo-realvalue0001* && $swapped != *demo-placeholder-0001* ]] ||
    { echo "FAIL $d: placeholder not swapped for httpbin.org: $swapped"; exit 1; }
  elsewhere=$(as_agent "curl -s -m 20 -H 'X-Demo-Token: demo-placeholder-0001' https://postman-echo.com/headers")
  [[ $elsewhere != *demo-realvalue0001* ]] || { echo "FAIL $d: secret leaked to another host"; exit 1; }
  denied=$(as_agent "curl -s -o /dev/null -w '%{http_code}' -m 20 http://10.255.255.1/")
  [[ $denied == 502 ]] || { echo "FAIL $d: private upstream not denied (got $denied)"; exit 1; }
  echo "secret swapped for its host only, private upstream denied"
  echo "=== $d: idempotence"
  expect_no_change "second run" "$(run "$name" site.yml -e devbox_mode=enforce)"
  echo "=== $d: check mode"
  expect_no_change "--check --diff" "$(run "$name" site.yml -e devbox_mode=enforce --check --diff)"
  echo "=== $d: uninstall"
  run "$name" uninstall.yml -e devbox_uninstall_agent_user=true -e devbox_uninstall_keep_cache=true
  left=$(podman exec "$name" bash -c 'nft list table inet agent_egress 2>/dev/null; id agent 2>/dev/null; ls -d /etc/iron-proxy /opt/agent-devbox 2>/dev/null; systemctl list-unit-files "iron-proxy*" "devbox-*" "agent-egress*" --no-legend 2>/dev/null' || true)
  [[ -z $left ]] || { echo "FAIL $d: uninstall left: $left"; exit 1; }
  echo "=== $d: OK"
  [[ ${KEEP:-0} == 1 ]] || podman rm -f "$name" >/dev/null
done
