#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/integrations/starrocks/docker-compose.yml"

cleanup() {
  docker compose -f "${COMPOSE_FILE}" down -v
}
trap cleanup EXIT

docker compose -f "${COMPOSE_FILE}" up -d

for attempt in $(seq 1 60); do
  health="$(docker inspect --format '{{.State.Health.Status}}' semantic-designer-starrocks 2>/dev/null || true)"
  if [[ "${health}" == "healthy" ]]; then
    break
  fi
  if [[ "${health}" == "unhealthy" ]]; then
    docker compose -f "${COMPOSE_FILE}" logs
    exit 1
  fi
  sleep 5
  if [[ "${attempt}" == "60" ]]; then
    docker compose -f "${COMPOSE_FILE}" logs
    echo "StarRocks did not become healthy within 5 minutes" >&2
    exit 1
  fi
done

PYTHONPATH="${ROOT_DIR}" python3 "${ROOT_DIR}/integrations/starrocks/test_starrocks.py"
