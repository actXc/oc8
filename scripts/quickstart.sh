#!/usr/bin/env bash
# Start a safe local oc8 Community evaluation instance on macOS or Linux.
# It creates only missing local secrets and never resets containers or volumes.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  printf 'Quickstart stopped: %s\n' "$1" >&2
  exit 1
}

case "$(uname -s)" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*)
    fail "Use PowerShell instead: .\\scripts\\quickstart.ps1"
    ;;
  *) fail "Unsupported platform. Use macOS, Linux, or scripts/quickstart.ps1 on Windows." ;;
esac

command -v openssl >/dev/null 2>&1 || fail "openssl is required to generate local secrets."

# Resolution order: an explicit env var wins, then a value already persisted
# in .env from a previous run, then auto-detect from what's on PATH.
resolve_container_runtime() {
  if [[ -n "${OC8_CONTAINER_RUNTIME:-}" ]]; then
    printf '%s\n' "$OC8_CONTAINER_RUNTIME"
    return
  fi
  if [[ -f .env ]]; then
    local from_env
    from_env="$(awk -F= '$1 == "OC8_CONTAINER_RUNTIME" { sub(/^[^=]*=/, ""); print; exit }' .env)"
    if [[ -n "$from_env" ]]; then
      printf '%s\n' "$from_env"
      return
    fi
  fi
  if command -v docker >/dev/null 2>&1; then
    printf 'docker\n'
  elif command -v podman >/dev/null 2>&1; then
    printf 'podman\n'
  else
    fail "Neither Docker nor Podman was found. Install one of them first."
  fi
}

CONTAINER_RUNTIME="$(resolve_container_runtime)"
case "$CONTAINER_RUNTIME" in
  docker|podman) ;;
  *) fail "OC8_CONTAINER_RUNTIME must be 'docker' or 'podman', got '$CONTAINER_RUNTIME'." ;;
esac

if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
  command -v docker >/dev/null 2>&1 || fail "Docker is required. Install Docker Desktop or Docker Engine first."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
  docker info >/dev/null 2>&1 || fail "Docker is installed but its daemon is not running. Start Docker first."
  COMPOSE_CMD=(docker compose)
else
  command -v podman >/dev/null 2>&1 || fail "Podman is required (OC8_CONTAINER_RUNTIME=podman). Install it first."
  if podman compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(podman compose)
  elif command -v podman-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(podman-compose)
  else
    fail "Podman Compose is required: install the 'podman compose' plugin (Podman v4+) or podman-compose."
  fi
  podman info >/dev/null 2>&1 || fail "Podman is installed but not ready. Run 'podman machine start' (macOS) or 'systemctl --user enable --now podman.socket' (Linux) first."
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env 2>/dev/null || true
  printf 'Created .env from .env.example.\n'
else
  printf 'Using existing .env; non-empty values will not be changed.\n'
fi

env_value() {
  awk -F= -v key="$1" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env
}

set_env_value_if_missing() {
  local key="$1"
  local value="$2"
  local current
  current="$(env_value "$key")"
  if [[ -n "$current" ]]; then
    return
  fi

  local temp_env
  temp_env="$(mktemp "${TMPDIR:-/tmp}/oc8-env.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ "^" key "=" { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' .env > "$temp_env"
  mv "$temp_env" .env
  chmod 600 .env 2>/dev/null || true
  printf 'Generated %s in .env.\n' "$key"
}

set_env_value_if_missing "OC8_JWT_SECRET" "$(openssl rand -hex 32)"
set_env_value_if_missing "OC8_SECRET_KEK" "$(openssl rand -base64 32 | tr -d '\n')"
set_env_value_if_missing "POSTGRES_PASSWORD" "$(openssl rand -hex 24)"
set_env_value_if_missing "OC8_SANDBOX_PROVISIONER_TOKEN" "$(openssl rand -hex 32)"
set_env_value_if_missing "OC8_CONTAINER_RUNTIME" "$CONTAINER_RUNTIME"

if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
  podman_socket="$(podman info --format '{{.Host.RemoteSocket.Path}}' 2>/dev/null || true)"
  [[ -n "$podman_socket" ]] || fail "Could not determine the Podman API socket path (podman info --format failed)."
  set_env_value_if_missing "OC8_CONTAINER_SOCKET" "$podman_socket"
fi

printf '\nBuilding and starting oc8 Community…\n'
if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
  docker compose up -d --build
else
  "${COMPOSE_CMD[@]}" up -d --build
fi

port_mapping="$(env_value OC8_HTTP_PORT)"
port_mapping="${port_mapping:-80}"
if [[ "$port_mapping" == *:* ]]; then
  host="${port_mapping%:*}"
  port="${port_mapping##*:}"
  # `0.0.0.0` is a listen address, not an address a local browser can request.
  [[ "$host" == "0.0.0.0" || "$host" == "::" ]] && host="127.0.0.1"
  url="http://${host}:${port}"
else
  url="http://localhost"
  [[ "$port_mapping" != "80" ]] && url="${url}:${port_mapping}"
fi

printf '\nWaiting for %s/health …\n' "$url"
if command -v curl >/dev/null 2>&1; then
  ready=0
  for _ in $(seq 1 60); do
    if curl --silent --fail --max-time 3 "${url}/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  if [[ "$ready" -ne 1 ]]; then
    "${COMPOSE_CMD[@]}" ps
    fail "oc8 did not become healthy in time. Inspect: ${COMPOSE_CMD[*]} logs -f backend"
  fi
fi

printf '\n✓ oc8 Community is running at %s\n' "$url"
printf 'Next: open the URL and create the local administrator account.\n'
printf 'Logs: %s logs -f backend\n' "${COMPOSE_CMD[*]}"
printf 'Stop later (keeps data): %s stop\n' "${COMPOSE_CMD[*]}"
