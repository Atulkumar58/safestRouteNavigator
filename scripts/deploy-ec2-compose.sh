#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ENV="$ROOT_DIR/.secrets/backend.env"
PIPELINE_ENV="$ROOT_DIR/.secrets/pipeline-a.env"
DOCKER=(docker)

on_error() {
  local status=$?
  echo "SafeRoute deployment failed (exit ${status}). Recent container logs:" >&2
  "${DOCKER[@]}" compose -f "$ROOT_DIR/compose.yaml" logs --tail=100 >&2 || true
  exit "$status"
}
trap on_error ERR

command -v docker >/dev/null || { echo "Docker is missing. Install Docker Engine and the Compose plugin first." >&2; exit 1; }
if ! docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
  "${DOCKER[@]}" info >/dev/null || { echo "Docker is unavailable, including through sudo." >&2; exit 1; }
fi
"${DOCKER[@]}" compose version >/dev/null 2>&1 || {
  echo "Docker Compose v2 plugin is missing. On Ubuntu, install it with: sudo apt-get update && sudo apt-get install -y docker-compose-v2" >&2
  exit 1
}
[[ -f "$BACKEND_ENV" ]] || { echo "Missing $BACKEND_ENV. Copy backend.env.example and fill in its values." >&2; exit 1; }
[[ -f "$PIPELINE_ENV" ]] || { echo "Missing $PIPELINE_ENV. Copy pipeline-a.env.example and fill in its values." >&2; exit 1; }

for check in "MONGODB_URI:$BACKEND_ENV" "API_BEARER_TOKEN:$BACKEND_ENV" "MONGODB_URI:$PIPELINE_ENV" "OPENAQ_API_KEY:$PIPELINE_ENV"; do
  key="${check%%:*}"
  file="${check#*:}"
  if ! grep -Eq "^${key}=.+" "$file"; then
    echo "Required non-empty ${key} is missing from $(basename "$file")." >&2
    exit 1
  fi
done

read_env_value() {
  local key="$1" file="$2"
  sed -n "s/^${key}=//p" "$file" | head -n 1
}
backend_uri="$(read_env_value MONGODB_URI "$BACKEND_ENV")"
pipeline_uri="$(read_env_value MONGODB_URI "$PIPELINE_ENV")"
api_token="$(read_env_value API_BEARER_TOKEN "$BACKEND_ENV")"
openaq_key="$(read_env_value OPENAQ_API_KEY "$PIPELINE_ENV")"
if [[ "$backend_uri" != "$pipeline_uri" ]]; then
  echo "Both services must use the same MONGODB_URI." >&2
  exit 1
fi
if [[ "$backend_uri" == *'<'* || ( "$backend_uri" != mongodb://* && "$backend_uri" != mongodb+srv://* ) ]]; then
  echo "MONGODB_URI still has a placeholder or is not a MongoDB URI." >&2
  exit 1
fi
if (( ${#api_token} < 32 )) || [[ "$api_token" == *'<'* ]]; then
  echo "API_BEARER_TOKEN must be a real secret with at least 32 characters." >&2
  exit 1
fi
if [[ "$openaq_key" == *'<'* ]]; then
  echo "OPENAQ_API_KEY still has its example placeholder." >&2
  exit 1
fi
unset backend_uri pipeline_uri api_token openaq_key

chmod 600 "$BACKEND_ENV" "$PIPELINE_ENV"
if [[ "$(awk '/MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)" -lt 1500 ]]; then
  echo "Warning: this host has under 1.5 GiB RAM; reduce Compose memory limits or use a larger EC2 size." >&2
fi

cd "$ROOT_DIR"
echo "Building the backend and Pipeline-A images..."
"${DOCKER[@]}" compose build backend
"${DOCKER[@]}" compose build pipeline-a

echo "Creating MongoDB indexes once before starting either service..."
"${DOCKER[@]}" compose run --rm --no-deps backend npm run db:ensure-indexes

echo "Starting both services..."
"${DOCKER[@]}" compose up -d --remove-orphans
"${DOCKER[@]}" compose ps
echo
echo "Backend health: http://127.0.0.1:3000/health/ready"
echo "Pipeline-A health (local host only): http://127.0.0.1:8081/health/ready"
echo "View live logs with: ${DOCKER[*]} compose logs -f --tail=100"
