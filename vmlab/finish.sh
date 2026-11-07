#!/usr/bin/env bash
# Finishes the VM lab after Ansible: names its kubeconfig context so BlindSpot's guard accepts it,
# installs pod networking, and waits for every node to be Ready.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
out=${OUT:-$here/out}
export KUBECONFIG=$out/kubeconfig

FLANNEL_URL=https://github.com/flannel-io/flannel/releases/download/v0.28.10/kube-flannel.yml
FLANNEL_SHA256=393bdbce53eb50c8eb97dd513b218518b7d6e7985dc7b889be956e112a711084

# BlindSpot only accepts contexts starting with kind- or blindspot-.
current=$(kubectl config current-context)
[ "$current" = blindspot-vm ] || kubectl config rename-context "$current" blindspot-vm

curl -fsSLo "$out/kube-flannel.yml" "$FLANNEL_URL"
echo "$FLANNEL_SHA256  $out/kube-flannel.yml" | sha256sum -c
kubectl apply -f "$out/kube-flannel.yml"

kubectl wait --for=condition=Ready nodes --all --timeout=10m
kubectl get nodes -o wide
