#!/bin/bash
# Run by verify.yml as the agent, inside the agent's slice, with the agent
# environment. Prints one "PASS|FAIL <check>: <detail>" line per check and
# exits non-zero if any check failed. Expectations depend on $DEVBOX_MODE.
# Args: <admin loopback port> <gateway port or 0> <management port> <DNS probe IP>
set -uo pipefail
admin_port=$1 gw_port=$2 mgmt_port=$3 dns_probe=$4
enforce=$([[ $DEVBOX_MODE == enforce ]] && echo 1 || echo 0)
fails=0
check() { # name expected actual
  if [[ $2 == "$3" ]]; then echo "PASS $1: $3"; else echo "FAIL $1: expected $2, got $3"; fails=$((fails + 1)); fi
}
when_enforce() { [[ $enforce == 1 ]] && echo "$1" || echo "$2"; }
# HTTP status, or the proxy's CONNECT status when the tunnel was refused.
code() {
  local http connect
  read -r http connect < <(curl -s -o /dev/null -m 20 -w '%{http_code} %{http_connect}\n' "$@" 2>/dev/null)
  if [[ ${http:-000} != 000 ]]; then echo "$http"; else echo "${connect:-000}"; fi
}
tcp() { timeout 5 bash -c "echo > /dev/tcp/$1/$2" 2>/dev/null && echo open || echo closed; }

# Egress through iron-proxy
check "allowlisted host via proxy" 200 "$(code https://registry.npmjs.org/left-pad)"
check "unknown host via proxy" "$(when_enforce 403 200)" "$(code https://example.com/)"
check "direct HTTPS is redirected to the proxy" 200 "$(code --noproxy '*' https://registry.npmjs.org/left-pad)"
# Refused either by the allowlist (403, enforce) or the upstream deny list (502).
check "proxy refuses loopback upstreams" "$(when_enforce 403 502)" "$(code --noproxy '' -x "$HTTPS_PROXY" "http://127.0.0.1:$admin_port/")"

# nftables policy
check "admin loopback service" "$(when_enforce closed open)" "$(tcp 127.0.0.1 "$admin_port")"
check "management API port" "$(when_enforce closed open)" "$(tcp 127.0.0.1 "$mgmt_port")"
check "direct DNS to $dns_probe" "$(when_enforce blocked answered)" \
  "$(timeout 6 python3 -I -c 'import socket,sys;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(4);s.sendto(bytes.fromhex("123401000001000000000000076578616d706c6503636f6d0000010001"),(sys.argv[1],53));s.recv(512);print("answered")' "$dns_probe" 2>/dev/null || echo blocked)"
check "own ephemeral server" ok "$(python3 -I -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));s.listen(1);socket.create_connection(s.getsockname(),timeout=3);print("ok")' 2>/dev/null || echo failed)"

# Secrets and privileges
for f in /etc/iron-proxy/secrets /etc/iron-proxy/ca/ca.key /etc/iron-proxy/management.env /etc/iron-proxy/proxy.yaml; do
  check "cannot read $f" denied "$([[ -r $f ]] && echo readable || echo denied)"
done
check "no general sudo" denied "$(sudo -n true 2>/dev/null && echo allowed || echo denied)"
if [[ -x /usr/local/sbin/agent-pkg-install ]]; then
  for arg in -o x.rpm ./x.deb 'pkg=1.0'; do
    out=$(sudo -n /usr/local/sbin/agent-pkg-install "$arg" 2>&1)
    check "pkg helper refuses '$arg'" refused "$([[ $out == *'refused package name'* ]] && echo refused || echo accepted)"
  done
fi

# Agent CLIs installed by the playbook start (through the proxy)
for cli in claude codex hermes; do
  if [[ -x $HOME/.local/bin/$cli ]]; then
    check "$cli --version" ok "$(timeout 60 "$HOME/.local/bin/$cli" --version >/dev/null 2>&1 && echo ok || echo failed)"
  fi
done

# Read-only gateway
if [[ $gw_port != 0 ]]; then
  check "gateway: Prometheus query" 200 "$(code --noproxy '*' "http://127.0.0.1:$gw_port/api/v1/query?query=up")"
  check "gateway: Loki labels" 200 "$(code --noproxy '*' "http://127.0.0.1:$gw_port/loki/api/v1/labels")"
  # The pipeline works end to end: metrics are scraped and logs arrive.
  # Retried: right after an install, Prometheus may not have scraped yet.
  for job in node alloy loki; do
    up=missing
    for _ in $(seq 12); do
      up=$(curl -s -m 10 --noproxy '*' -G "http://127.0.0.1:$gw_port/api/v1/query" \
        --data-urlencode "query=up{job=\"$job\"}" |
        python3 -I -c 'import json,sys;r=json.load(sys.stdin)["data"]["result"];print(r[0]["value"][1] if r else "missing")')
      [[ $up == 1 ]] && break
      sleep 5
    done
    check "Prometheus scrapes $job" 1 "$up"
  done
  logs=missing
  for _ in $(seq 12); do
    logs=$(curl -s -m 10 --noproxy '*' -G "http://127.0.0.1:$gw_port/loki/api/v1/query_range" \
      --data-urlencode 'query={unit="iron-proxy.service"}' --data-urlencode limit=1 |
      python3 -I -c 'import json,sys;print("present" if json.load(sys.stdin)["data"]["result"] else "missing")')
    [[ $logs == present ]] && break
    sleep 5
  done
  check "Loki has iron-proxy logs" present "$logs"
  units=missing
  for _ in $(seq 12); do
    units=$(curl -s -m 10 --noproxy '*' -G "http://127.0.0.1:$gw_port/api/v1/query" \
      --data-urlencode 'query=min(devbox_unit_active)' |
      python3 -I -c 'import json,sys;r=json.load(sys.stdin)["data"]["result"];print("all active" if r and r[0]["value"][1] == "1" else "missing or down")')
    [[ $units == "all active" ]] && break
    sleep 5
  done
  check "Prometheus sees every devbox unit active" "all active" "$units"
  check "gateway: Loki push refused" 403 "$(code --noproxy '*' -X POST "http://127.0.0.1:$gw_port/loki/api/v1/push")"
  check "gateway: encoded path refused" 403 "$(code --noproxy '*' "http://127.0.0.1:$gw_port/loki/api/v1/label/..%2F..%2Fpush/values")"
fi
echo "agent checks: $fails failed"
exit $(( fails > 0 ))
