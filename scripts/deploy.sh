#!/usr/bin/env bash
# Real-cluster deploy path.
#
# Builds each image locally, streams it over SSH into the containerd image store
# of every node (no registry, no docker daemon on the node), then applies the
# manifests and waits for each deployment to roll out.
#
# Credentials are never defaulted: the node addresses, the SSH user and the key
# are always arguments, so nothing about one environment is baked into the repo.
#
# Usage:
#   scripts/deploy.sh --nodes <ip[,ip...]> --ssh-user <user> --ssh-key <path> \
#                     [--namespace kubeheal-system]
set -euo pipefail

SERVICES=(sim chaos graph faultlib agent journeys)
ROLLOUT_TIMEOUT=180s
NAMESPACE=kubeheal-system

REPO_ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"

usage() {
    cat <<'EOF'
Usage: scripts/deploy.sh --nodes <ip[,ip...]> --ssh-user <user>
                         --ssh-key <path> [--namespace kubeheal-system]

  --nodes      comma-separated node addresses; an image is imported on each
  --ssh-user   user that can reach every node over SSH
  --ssh-key    private key used for SSH
  --namespace  target namespace (the manifests pin kubeheal-system)
EOF
}

die() {
    echo "deploy: $*" >&2
    exit 2
}

NODES=""
SSH_USER=""
SSH_KEY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --nodes)     [ $# -ge 2 ] || die "--nodes needs a value"; NODES="$2"; shift 2 ;;
        --ssh-user)  [ $# -ge 2 ] || die "--ssh-user needs a value"; SSH_USER="$2"; shift 2 ;;
        --ssh-key)   [ $# -ge 2 ] || die "--ssh-key needs a value"; SSH_KEY="$2"; shift 2 ;;
        --namespace) [ $# -ge 2 ] || die "--namespace needs a value"; NAMESPACE="$2"; shift 2 ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "unknown argument: $1 (see --help)" ;;
    esac
done

[ -n "$NODES" ] || { usage >&2; die "--nodes is required"; }
[ -n "$SSH_USER" ] || { usage >&2; die "--ssh-user is required"; }
[ -n "$SSH_KEY" ] || { usage >&2; die "--ssh-key is required"; }

[ -f "$SSH_KEY" ] || die "--ssh-key: no such file: $SSH_KEY"
[ -r "$SSH_KEY" ] || die "--ssh-key: not readable: $SSH_KEY"

case "$SSH_USER" in
    *" "*|*"@"*) die "--ssh-user must be a bare user name, got: $SSH_USER" ;;
esac

# The committed manifests pin `namespace: kubeheal-system` and hardcode service
# DNS inside that namespace, so a different value would half-apply and then fail
# to resolve. Refuse instead of silently deploying to the wrong place.
if [ "$NAMESPACE" != "kubeheal-system" ]; then
    die "--namespace $NAMESPACE is not supported: the manifests pin kubeheal-system"
fi

IFS=',' read -r -a NODE_LIST <<< "$NODES"
[ "${#NODE_LIST[@]}" -gt 0 ] || die "--nodes is empty"

for node in "${NODE_LIST[@]}"; do
    [ -n "$node" ] || die "--nodes has an empty entry: $NODES"
    case "$node" in
        *[!A-Za-z0-9._:-]*) die "--nodes entry is not a host or address: $node" ;;
    esac
done

for tool in docker ssh kubectl; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is not on PATH"
done

echo "==> context: $(kubectl config current-context)"
echo "==> nodes: ${NODE_LIST[*]}"
echo "==> namespace: $NAMESPACE"

for svc in "${SERVICES[@]}" dashboard; do
    echo "==> build kubeheal-$svc:latest"
    docker build -t "kubeheal-$svc:latest" "$svc"
done

# One SSH session per image per node. The first contact trusts an unseen host
# key; pin the node host keys in known_hosts if that matters to you.
for node in "${NODE_LIST[@]}"; do
    for svc in "${SERVICES[@]}" dashboard; do
        echo "==> import kubeheal-$svc:latest on $node"
        docker save "kubeheal-$svc:latest" |
            ssh -i "$SSH_KEY" "$SSH_USER@$node" 'sudo ctr -n k8s.io images import -'
    done
done

echo "==> apply namespace"
kubectl apply -f k8s/namespace.yaml

echo "==> apply RBAC"
kubectl apply -f chaos/k8s/rbac.yaml

echo "==> apply persistent volume claims"
kubectl apply -f chaos/k8s/pvc.yaml
kubectl apply -f agent/k8s/pvc.yaml

echo "==> apply deployments and services"
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
