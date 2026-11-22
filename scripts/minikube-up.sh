#!/usr/bin/env bash
# Local cluster gate path.
#
# Builds every image, loads it into the running minikube node, applies the
# manifests and waits for each deployment to roll out. Images are never pulled:
# every Deployment uses `imagePullPolicy: Never`, so a missing tag surfaces as a
# clear rollout failure rather than an ImagePullBackOff.
#
# Usage: scripts/minikube-up.sh [--skip-build]
#   --skip-build   reuse the images already tagged in the local daemon
set -euo pipefail

SERVICES=(sim chaos graph faultlib agent journeys)
NAMESPACE=kubeheal-system
ROLLOUT_TIMEOUT=180s

REPO_ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"

usage() {
    cat <<'EOF'
Usage: scripts/minikube-up.sh [--skip-build]

  --skip-build   reuse the images already tagged in the local daemon
EOF
}

SKIP_BUILD=false
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "minikube-up: unknown argument: $arg" >&2; exit 2 ;;
    esac
done

for tool in docker minikube kubectl; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "minikube-up: $tool is not on PATH" >&2
        exit 1
    }
done

echo "==> context: $(kubectl config current-context)"

if [ "$SKIP_BUILD" = false ]; then
    for svc in "${SERVICES[@]}" dashboard; do
        echo "==> build kubeheal-$svc:latest"
        docker build -t "kubeheal-$svc:latest" "$svc"
    done
fi

for svc in "${SERVICES[@]}" dashboard; do
    echo "==> load kubeheal-$svc:latest into minikube"
    minikube image load "kubeheal-$svc:latest"
done

echo "==> apply namespace"
kubectl apply -f k8s/namespace.yaml

# The workload the demo faults. It has a Service on purpose: the graph emits one
# node per Service, so a bare Deployment would have no node to match and blast
# radius would stay empty for a perfectly real fault.
echo "==> apply demo target"
kubectl apply -f k8s/demo-target.yaml

# RBAC and PVCs live beside the workloads, so each directory apply below covers
# them; the namespace has to exist first because every object names it.
echo "==> apply services"
for svc in "${SERVICES[@]}"; do
    kubectl apply -f "$svc/k8s/"
done
kubectl apply -f dashboard/k8s/

for svc in "${SERVICES[@]}" dashboard; do
    echo "==> wait for deploy/kubeheal-$svc"
    kubectl -n "$NAMESPACE" rollout status "deploy/kubeheal-$svc" --timeout="$ROLLOUT_TIMEOUT"
done

echo
kubectl -n "$NAMESPACE" get pods
