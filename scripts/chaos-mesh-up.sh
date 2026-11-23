#!/usr/bin/env bash
# Install Chaos Mesh so a real fault can be injected.
#
# Two things here are not the obvious defaults, and both were learned the hard
# way on a cluster:
#
# 1. The CRDs are applied by kubectl with --server-side. Three of the 23 CRDs
#    exceed the 262144-byte annotation limit that client-side apply enforces.
# 2. The chart installs with --skip-crds and WITHOUT --wait. The chart's own CRD
#    apply conflicts with the ones above on
#    `controller-gen.kubebuilder.io/version`, and `--wait` cannot survive the
#    image pulls on a slow link (one pull was measured at 7m19s). `rollout
#    status` is the completion signal instead.
#
# Idempotent: re-running applies the CRDs again and upgrades the release.
set -euo pipefail

NAMESPACE="${NAMESPACE:-chaos-mesh}"
RELEASE="${RELEASE:-chaos-mesh}"
CHART_VERSION="${CHART_VERSION:-2.8.8}"
CRD_VERSION="${CRD_VERSION:-v2.8.8}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-1500s}"

for tool in kubectl helm curl; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "error: $tool is required but not on PATH" >&2
    exit 2
  }
done

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> apply Chaos Mesh ${CRD_VERSION} CRDs (server-side)"
curl -fsSL --retry 3 \
  "https://raw.githubusercontent.com/chaos-mesh/chaos-mesh/${CRD_VERSION}/manifests/crd.yaml" \
  -o "$tmp/crd.yaml"
kubectl apply --server-side --force-conflicts -f "$tmp/crd.yaml" >/dev/null

echo "==> install controller (chart ${CHART_VERSION}, skip-crds, no --wait)"
helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
helm repo update chaos-mesh >/dev/null 2>&1 || true
helm upgrade --install "$RELEASE" chaos-mesh/chaos-mesh \
  --namespace "$NAMESPACE" --create-namespace \
  --version "$CHART_VERSION" \
  --skip-crds \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.create=false

echo "==> wait for the controller (pulls can take minutes)"
kubectl -n "$NAMESPACE" rollout status deploy/"$RELEASE"-controller-manager --timeout="$ROLLOUT_TIMEOUT"

echo "==> CRDs:"
kubectl get crd podchaos.chaos-mesh.org networkchaos.chaos-mesh.org iochaos.chaos-mesh.org \
  -o custom-columns=NAME:.metadata.name,ESTABLISHED:.status.conditions[0].status --no-headers

echo "==> pods:"
kubectl -n "$NAMESPACE" get pods
