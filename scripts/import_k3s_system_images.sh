#!/usr/bin/env bash
set -Eeuo pipefail

for command in docker kubectl python3 sudo; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  fi
done
if ! docker info >/dev/null 2>&1; then
  printf 'Docker Desktop WSL Integration is not available.\n' >&2
  exit 1
fi
if ! kubectl get --raw=/readyz >/dev/null 2>&1; then
  printf 'Kubernetes API is not ready.\n' >&2
  exit 1
fi

mapfile -t images < <(
  kubectl -n kube-system get pods -o json |
    python3 -c 'import json,sys; data=json.load(sys.stdin); print("\n".join(c["image"] for p in data["items"] for c in p["spec"]["containers"]))'
)
sandbox_image="$(
  sudo sed -n 's/^[[:space:]]*sandbox = "\([^"]*\)"/\1/p' \
    /var/lib/rancher/k3s/agent/etc/containerd/config.toml | head -n 1
)"
[[ -n "$sandbox_image" ]] && images+=("$sandbox_image")

mapfile -t images < <(printf '%s\n' "${images[@]}" | sed '/^$/d' | sort -u)
if (( ${#images[@]} == 0 )); then
  printf 'No K3s system images were discovered.\n' >&2
  exit 1
fi

printf 'Pulling %d K3s system image(s) with Docker Desktop...\n' "${#images[@]}"
for image in "${images[@]}"; do
  docker pull "$image"
done

printf 'Importing images into K3s containerd...\n'
docker save "${images[@]}" | sudo k3s ctr images import -
printf 'K3s system images imported successfully.\n'

