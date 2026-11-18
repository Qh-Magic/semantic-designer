#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/integrations/starrocks/docker-compose.yml"

cleanup() {
  docker compose -f "${COMPOSE_FILE}" down -v
}
trap cleanup EXIT

docker compose -f "${COMPOSE_FILE}" up -d
docker compose -f "${COMPOSE_FILE}" wait starrocks

PYTHONPATH="${ROOT_DIR}" python3 "${ROOT_DIR}/integrations/starrocks/test_starrocks.py"
