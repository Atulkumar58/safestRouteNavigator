#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SECRET_FILE=""
TOKEN_FILE="${HOME}/saferoute-api-token.txt"

kube() {
  sudo k3s kubectl "$@"
}

cleanup() {
  [[ -z "$SECRET_FILE" ]] || rm -f -- "$SECRET_FILE"
}

on_error() {
  local status=$?
  echo "Deployment stopped with exit code ${status}." >&2
  if kube get namespace saferoute >/dev/null 2>&1; then
    kube -n saferoute get pods -o wide >&2 || true
    kube -n saferoute get events --sort-by=.lastTimestamp >&2 || true
    if kube -n saferoute get job saferoute-db-indexes >/dev/null 2>&1; then
      kube -n saferoute logs job/saferoute-db-indexes >&2 || true
    fi
  fi
  exit "$status"
}

trap cleanup EXIT
trap on_error ERR

command -v docker >/dev/null || { echo "Install Docker first (docker command missing)." >&2; exit 1; }
command -v openssl >/dev/null || { echo "Install openssl first." >&2; exit 1; }
sudo k3s kubectl get nodes >/dev/null 2>&1 || {
  echo "K3s is unavailable; check that the service is running." >&2
  exit 1
}
RAM_MIB="$(awk '/MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
if (( RAM_MIB < 3500 )); then
  echo "This deployment profile needs an EC2 size with 4 GiB RAM (at least 3500 MiB visible to Linux); detected ${RAM_MIB} MiB." >&2
  echo "Resize the EC2 instance before deleting or redeploying the namespace." >&2
  exit 1
fi
echo "Kubernetes node allocatable resources:"
kube get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,MEMORY:.status.allocatable.memory

echo "This resets Kubernetes resources in namespace saferoute on this EC2 host."
echo "It does not delete MongoDB Atlas databases or data."
read -r -p "Type RESET-SAFEROUTE to continue: " confirmation
[[ "$confirmation" == "RESET-SAFEROUTE" ]] || { echo "Cancelled."; exit 1; }

rm -f -- "$ROOT_DIR/k8s/secrets.yaml"

echo "Enter a MongoDB URI for a NEW, empty database name. Rotate any credentials previously shared."
read -r -s -p "MongoDB URI: " MONGODB_URI; echo
read -r -s -p "Rotated OpenAQ API key: " OPENAQ_API_KEY; echo
[[ -n "$MONGODB_URI" && -n "$OPENAQ_API_KEY" ]] || {
  echo "MongoDB URI and OpenAQ key are required." >&2
  exit 1
}
[[ "$MONGODB_URI" == mongodb://* || "$MONGODB_URI" == mongodb+srv://* ]] || {
  echo "MongoDB URI must start with mongodb:// or mongodb+srv://." >&2
  exit 1
}
[[ "$MONGODB_URI" =~ ^mongodb(\+srv)?://[^/]+/[^?]+ ]] || {
  echo "MongoDB URI must include a database name in its path." >&2
  exit 1
}

umask 077
SECRET_FILE="$(mktemp)"
API_BEARER_TOKEN="$(openssl rand -hex 32)"
printf 'MONGODB_URI=%s\nOPENAQ_API_KEY=%s\nAPI_BEARER_TOKEN=%s\n' \
  "$MONGODB_URI" "$OPENAQ_API_KEY" "$API_BEARER_TOKEN" > "$SECRET_FILE"
printf '%s' "$API_BEARER_TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
unset MONGODB_URI OPENAQ_API_KEY API_BEARER_TOKEN

cd "$ROOT_DIR"
echo "Building SafeRoute images..."
sudo docker build -t saferoute-backend:latest .
sudo docker build -t saferoute-pipeline-a:latest -f Pipeline-A/Dockerfile Pipeline-A
sudo docker save -o /tmp/saferoute-images.tar \
  saferoute-backend:latest saferoute-pipeline-a:latest
sudo k3s ctr images import /tmp/saferoute-images.tar
rm -f /tmp/saferoute-images.tar

echo "Removing the previous Kubernetes namespace and its app resources..."
kube delete namespace saferoute --ignore-not-found=true --wait=true --timeout=180s
kube apply -f k8s/namespace.yaml
kube create secret generic saferoute-secrets -n saferoute \
  --from-env-file="$SECRET_FILE" --dry-run=client -o yaml | kube apply -f -
cleanup
SECRET_FILE=""
kube apply -f k8s/configmap.yaml

echo "Creating MongoDB indexes once before starting replicas..."
kube apply -f k8s/db-indexes-job.yaml
if ! kube -n saferoute wait --for=condition=complete job/saferoute-db-indexes --timeout=5m; then
  kube -n saferoute logs job/saferoute-db-indexes >&2 || true
  echo "Index setup failed. The app Deployments were not started." >&2
  exit 1
fi

echo "Starting backend, Pipeline A, and Traefik ingress..."
kube apply -f k8s/backend.yaml
kube apply -f k8s/pipeline-a.yaml
kube apply -f k8s/ingress.yaml
kube -n saferoute rollout status deployment/saferoute-backend --timeout=5m
kube -n saferoute rollout status deployment/saferoute-pipeline-a --timeout=5m
kube -n saferoute get pods,services,ingress -o wide

echo "API bearer token saved with mode 600 at: ${TOKEN_FILE}"
echo "Do not embed this shared token in a public frontend or send it over HTTP."
