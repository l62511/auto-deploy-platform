#!/usr/bin/env bash
set -Eeuo pipefail

ENGINE="${1:-compose}"
DISK_THRESHOLD="${DISK_THRESHOLD:-85}"
NETWORK_TARGET="${NETWORK_TARGET:-}"
REQUIRED_FREE_PORTS="${REQUIRED_FREE_PORTS:-}"
CHECK_DOCKER="${CHECK_DOCKER:-1}"
PROJECT_PATH="${PROJECT_PATH:-.}"
failures=0

ok() { printf '[OK] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; failures=$((failures + 1)); }

if ! [[ "$DISK_THRESHOLD" =~ ^[0-9]+$ ]] || (( DISK_THRESHOLD < 1 || DISK_THRESHOLD > 100 )); then
  fail "DISK_THRESHOLD must be an integer from 1 to 100"
else
  disk_usage="$(df -P "$PROJECT_PATH" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
  if [[ -n "$disk_usage" ]] && (( disk_usage < DISK_THRESHOLD )); then
    ok "Disk usage ${disk_usage}% is below threshold ${DISK_THRESHOLD}%"
  else
    fail "Disk usage ${disk_usage:-unknown}% reached threshold ${DISK_THRESHOLD}%"
  fi
fi

if [[ "$CHECK_DOCKER" == "1" ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker daemon is available"
  else
    fail "Docker daemon is unavailable"
  fi
fi

if [[ "$ENGINE" == "k8s" ]]; then
  if command -v kubectl >/dev/null 2>&1 && kubectl get --raw=/readyz >/dev/null 2>&1; then
    ok "Kubernetes API is ready"
  else
    fail "kubectl is missing or the Kubernetes API is not ready"
  fi
fi

if [[ -n "$NETWORK_TARGET" ]]; then
  if command -v curl >/dev/null 2>&1 && curl --silent --show-error --fail --max-time 5 "$NETWORK_TARGET" >/dev/null; then
    ok "Network target is reachable: $NETWORK_TARGET"
  else
    fail "Network target is unreachable: $NETWORK_TARGET"
  fi
fi

IFS=',' read -ra ports <<< "$REQUIRED_FREE_PORTS"
for port in "${ports[@]}"; do
  port="${port//[[:space:]]/}"
  [[ -z "$port" ]] && continue
  if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    fail "Invalid TCP port: $port"
  elif ss -ltnH | awk '{print $4}' | grep -Eq "(^|:)${port}$"; then
    fail "TCP port is already in use: $port"
  else
    ok "TCP port is available: $port"
  fi
done

if (( failures > 0 )); then
  printf 'Environment precheck failed with %d error(s).\n' "$failures" >&2
  exit 1
fi
ok "Environment precheck completed for engine: $ENGINE"

