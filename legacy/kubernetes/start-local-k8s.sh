#!/usr/bin/env bash
set -Eeuo pipefail

SAFEROUTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAFEROUTE_CLUSTER="${SAFEROUTE_KIND_CLUSTER:-saferoute}"
SAFEROUTE_CONTEXT="kind-${SAFEROUTE_CLUSTER}"
SAFEROUTE_NAMESPACE="saferoute"
SAFEROUTE_K8S_DIR="${SAFEROUTE_ROOT}/k8s"
SAFEROUTE_LOG_DIR="${SAFEROUTE_ROOT}/logs"
SAFEROUTE_LOG_FILE="${SAFEROUTE_LOG_DIR}/k8s-start-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "${SAFEROUTE_LOG_DIR}"
exec > >(tee -a "${SAFEROUTE_LOG_FILE}") 2>&1

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

redact_logs() {
  sed -E \
    -e 's#mongodb([+]srv)?://[^[:space:]]+#[REDACTED_MONGODB_URI]#g' \
    -e 's#([?&]key=)[^&[:space:]]+#\1[REDACTED]#g' \
    -e 's#(Bearer )[[:alnum:]_.~-]+#\1[REDACTED]#g' \
    -e 's#(OPENAQ_API_KEY[=:[:space:]]+)[^[:space:]]+#\1[REDACTED]#g'
}

collect_diagnostics() {
  log "Collecting Kubernetes diagnostics"
  kubectl --context "${SAFEROUTE_CONTEXT}" get nodes -o wide 2>&1 | redact_logs || true
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" get pods -o wide 2>&1 | redact_logs || true
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" get events --sort-by=.lastTimestamp 2>&1 | tail -40 | redact_logs || true

  local pod_name
  while IFS= read -r pod_name; do
    [[ -n "${pod_name}" ]] || continue
    log "Recent logs: ${pod_name}"
    kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" logs "${pod_name}" --all-containers=true --tail=80 2>&1 | redact_logs || true
    kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" logs "${pod_name}" --all-containers=true --previous --tail=80 2>&1 | redact_logs || true
  done < <(kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" get pods -o name 2>/dev/null || true)
}

on_error() {
  local status=$?
  trap - ERR
  log "ERROR: command failed (exit ${status}): ${BASH_COMMAND}"
  collect_diagnostics
  log "Full run log: ${SAFEROUTE_LOG_FILE}"
  exit "${status}"
}
trap on_error ERR

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    log "Required command not found: $1"
    return 1
  }
}

ensure_cluster() {
  if kind get clusters 2>/dev/null | grep -Fxq "${SAFEROUTE_CLUSTER}"; then
    log "Kind cluster '${SAFEROUTE_CLUSTER}' already exists; starting stopped node containers if needed"
    local container_id running
    while IFS= read -r container_id; do
      [[ -n "${container_id}" ]] || continue
      running="$(docker inspect -f '{{.State.Running}}' "${container_id}")"
      if [[ "${running}" != "true" ]]; then
        docker start "${container_id}"
      fi
    done < <(docker ps -aq --filter "label=io.x-k8s.kind.cluster=${SAFEROUTE_CLUSTER}")
  else
    log "Creating two-node Kind cluster '${SAFEROUTE_CLUSTER}'"
    kind create cluster \
      --name "${SAFEROUTE_CLUSTER}" \
      --config "${SAFEROUTE_K8S_DIR}/kind-config.yaml" \
      --wait 180s
  fi

  kubectl config use-context "${SAFEROUTE_CONTEXT}"
  kubectl --context "${SAFEROUTE_CONTEXT}" wait --for=condition=Ready nodes --all --timeout=180s

  # Kind taints its control-plane node by default. Local Pipeline A needs its
  # two replicas on separate nodes, so permit workloads on this dev node.
  kubectl --context "${SAFEROUTE_CONTEXT}" taint nodes \
    "${SAFEROUTE_CLUSTER}-control-plane" \
    node-role.kubernetes.io/control-plane- 2>/dev/null || true
}

ensure_secret() {
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/namespace.yaml"

  local secret_exists=false
  if kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    get secret saferoute-secrets >/dev/null 2>&1; then
    secret_exists=true
  fi

  if [[ -f "${SAFEROUTE_K8S_DIR}/secrets.yaml" ]] && \
    ! grep -q 'REPLACE_WITH_' "${SAFEROUTE_K8S_DIR}/secrets.yaml"; then
    log "Applying the populated, ignored k8s/secrets.yaml without printing values"
    kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/secrets.yaml"
  elif [[ "${secret_exists}" == "false" ]]; then
    log "Secret is missing or still a template. Copy k8s/secrets.example.yaml to k8s/secrets.yaml, fill in real credentials, then rerun."
    return 1
  else
    log "Reusing the existing saferoute-secrets Secret without reading or printing its values"
  fi

  local encoded_uri mongo_uri encoded_value
  encoded_uri="$(kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    get secret saferoute-secrets -o jsonpath='{.data.MONGODB_URI}')"
  [[ -n "${encoded_uri}" ]] || { log "Secret key MONGODB_URI is missing"; return 1; }
  mongo_uri="$(printf '%s' "${encoded_uri}" | base64 --decode)"
  case "${mongo_uri}" in
    mongodb://*|mongodb+srv://*) ;;
    *) log "MONGODB_URI must start with mongodb:// or mongodb+srv:// (value not shown)"; return 1 ;;
  esac

  for secret_key in OPENAQ_API_KEY API_BEARER_TOKEN; do
    encoded_value="$(kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
      get secret saferoute-secrets -o "jsonpath={.data.${secret_key}}")"
    [[ -n "${encoded_value}" ]] || { log "Secret key ${secret_key} is missing"; return 1; }
    local decoded_value
    decoded_value="$(printf '%s' "${encoded_value}" | base64 --decode)"
    if [[ "${decoded_value}" == REPLACE_* ]]; then
      log "Secret key ${secret_key} still has its template placeholder"
      return 1
    fi
    if [[ "${secret_key}" == "API_BEARER_TOKEN" && ${#decoded_value} -lt 32 ]]; then
      log "API_BEARER_TOKEN must contain at least 32 characters; value not shown"
      return 1
    fi
  done

  if [[ "${mongo_uri}" == *REPLACE_WITH* ]]; then
    log "MONGODB_URI still has its template placeholder (value not shown)"
    return 1
  fi
}

build_and_load_images() {
  if [[ "${SAFEROUTE_BUILD_IMAGES:-1}" == "1" ]]; then
    log "Building Pipeline B backend image"
    docker build -t saferoute-backend:latest "${SAFEROUTE_ROOT}"
    log "Building Pipeline A worker image"
    docker build -t saferoute-pipeline-a:latest \
      -f "${SAFEROUTE_ROOT}/Pipeline-A/Dockerfile" "${SAFEROUTE_ROOT}/Pipeline-A"
  else
    log "Skipping image builds (SAFEROUTE_BUILD_IMAGES=0)"
  fi

  kind load docker-image saferoute-backend:latest --name "${SAFEROUTE_CLUSTER}"
  kind load docker-image saferoute-pipeline-a:latest --name "${SAFEROUTE_CLUSTER}"
}

main() {
  require_command docker
  require_command kind
  require_command kubectl
  docker info >/dev/null

  log "Startup log: ${SAFEROUTE_LOG_FILE}"
  ensure_cluster
  ensure_secret
  build_and_load_images

  log "Applying ConfigMap and preparing MongoDB indexes once"
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/configmap.yaml"
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    delete job saferoute-db-indexes --ignore-not-found=true
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/db-indexes-job.yaml"
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    wait --for=condition=complete job/saferoute-db-indexes --timeout=300s

  log "Applying backend, Pipeline A, HPA, and Ingress"
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/backend.yaml"
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/backend-hpa.yaml"
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/pipeline-a.yaml"
  kubectl --context "${SAFEROUTE_CONTEXT}" apply -f "${SAFEROUTE_K8S_DIR}/ingress.yaml"

  log "Restarting deployments so updated images, ConfigMaps, and Secrets are loaded"
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    rollout restart deployment/saferoute-backend deployment/saferoute-pipeline-a

  log "Waiting for backend rollout"
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    rollout status deployment/saferoute-backend --timeout=300s
  log "Waiting for Pipeline A worker and standby"
  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" \
    rollout status deployment/saferoute-pipeline-a --timeout=300s

  kubectl --context "${SAFEROUTE_CONTEXT}" -n "${SAFEROUTE_NAMESPACE}" get pods,services,ingress -o wide
  log "SafeRoute is running. API port-forward: kubectl -n saferoute port-forward service/saferoute-backend 3000:80"
  log "Errors and startup output are recorded in ${SAFEROUTE_LOG_FILE}"
}

main "$@"
