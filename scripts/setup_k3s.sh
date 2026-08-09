#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  printf 'Run this script as a normal sudo-enabled user, not root.\n' >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y curl
fi

K3S_MIRROR="${INSTALL_K3S_MIRROR:-cn}"
if [[ "$K3S_MIRROR" == "cn" ]]; then
  install_url="https://rancher-mirror.rancher.cn/k3s/k3s-install.sh"
  export INSTALL_K3S_MIRROR=cn
else
  install_url="https://get.k3s.io"
fi

curl -sfL "$install_url" | sh -s - \
  --write-kubeconfig-mode 644 \
  --disable traefik

mkdir -p "$HOME/.kube"
sudo cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo chown "$(id -u):$(id -g)" "$HOME/.kube/config"
printf 'K3s installed. Verify with: kubectl get nodes\n'
