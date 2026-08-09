#!/usr/bin/env bash
set -Eeuo pipefail

REGISTRY_ADDRESS="${REGISTRY_ADDRESS:-localhost:5000}"
if ! [[ "$REGISTRY_ADDRESS" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  printf 'Invalid registry address: %s\n' "$REGISTRY_ADDRESS" >&2
  exit 1
fi
temporary_file="$(mktemp)"
trap 'rm -f "$temporary_file"' EXIT

sed "s|REGISTRY_ADDRESS|$REGISTRY_ADDRESS|g" > "$temporary_file" <<'EOF'
mirrors:
  "REGISTRY_ADDRESS":
    endpoint:
      - "http://REGISTRY_ADDRESS"
configs:
  "REGISTRY_ADDRESS":
    tls:
      insecure_skip_verify: true
EOF

sudo install -d -m 0755 /etc/rancher/k3s
sudo install -m 0644 "$temporary_file" /etc/rancher/k3s/registries.yaml
sudo systemctl restart k3s
printf 'K3s registry configured: %s\n' "$REGISTRY_ADDRESS"
