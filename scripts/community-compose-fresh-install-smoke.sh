#!/usr/bin/env bash
# Exercise the base Community Compose distribution as an external operator.
# It deliberately creates a unique Compose project and removes only that
# project's containers, networks, and named volumes on exit.
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$root_dir/docker-compose.yml"
run_id="$(date +%s)-$$"
project="oc8-community-smoke-${run_id}"
scratch_dir="$(mktemp -d "${TMPDIR:-/tmp}/oc8-community-smoke.XXXXXX")"
runtime_dir="$scratch_dir/sessions"
host_port="${OC8_SMOKE_HTTP_PORT:-}"
compose_started=false

cleanup() {
  local exit_code=$?
  if [[ "$compose_started" == true ]]; then
    docker compose --project-name "$project" --file "$compose_file" down --volumes --remove-orphans || true
  fi
  rm -rf "$scratch_dir"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

for command in docker curl openssl python3; do
  require_command "$command"
done
docker compose version >/dev/null

if [[ -z "$host_port" ]]; then
  host_port="$(python3 - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
fi
case "$host_port" in
  ''|*[!0-9]*)
    echo "OC8_SMOKE_HTTP_PORT must be a numeric, unused local port." >&2
    exit 1
    ;;
esac

mkdir -p "$runtime_dir"
chmod 700 "$scratch_dir" "$runtime_dir"

jwt_secret="$(openssl rand -hex 32)"
secret_kek="$(openssl rand -base64 32 | tr -d '\n')"
postgres_password="$(openssl rand -hex 16)"
provisioner_token="$(openssl rand -hex 32)"
base_url="http://127.0.0.1:${host_port}"

echo "Starting isolated Community smoke project: $project"
compose_started=true
OC8_ENV=prod \
OC8_SEED_ON_START=false \
OC8_HTTP_PORT="127.0.0.1:${host_port}" \
OC8_JWT_SECRET="$jwt_secret" \
OC8_SECRET_KEK="$secret_kek" \
POSTGRES_PASSWORD="$postgres_password" \
OC8_SANDBOX_PROVISIONER_TOKEN="$provisioner_token" \
OC8_RUNTIME_SESSION_ROOT="$runtime_dir" \
docker compose --project-name "$project" --file "$compose_file" up --detach --build

for _attempt in $(seq 1 90); do
  if curl --fail --silent --show-error "$base_url/health" >/dev/null; then
    break
  fi
  sleep 2
done
curl --fail --silent --show-error "$base_url/health" >/dev/null

config="$(curl --fail --silent --show-error "$base_url/api/v1/auth/config")"
python3 -c '
import json, sys
config = json.load(sys.stdin)
assert config == {
    "mode": "community",
    "authServerUrl": "",
    "realm": "",
    "clientId": "",
    "initialized": False,
}, config
' <<<"$config"

admin_email="smoke-admin-${run_id}@example.invalid"
admin_password="SmokePass-$(openssl rand -hex 12)"
setup="$(curl --fail --silent --show-error \
  --request POST "$base_url/api/v1/auth/setup" \
  --header 'content-type: application/json' \
  --data "{\"email\":\"${admin_email}\",\"password\":\"${admin_password}\",\"displayName\":\"Community Smoke Admin\"}")"
python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body["principal"]["subject"].endswith("@example.invalid"), body
assert body["principal"]["role"] == "org_admin", body
assert body["token"], body
' <<<"$setup"

login="$(curl --fail --silent --show-error \
  --request POST "$base_url/api/v1/auth/login" \
  --header 'content-type: application/json' \
  --data "{\"email\":\"${admin_email}\",\"password\":\"${admin_password}\"}")"
python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body["principal"]["subject"].endswith("@example.invalid"), body
assert body["principal"]["role"] == "org_admin", body
assert body["token"], body
' <<<"$login"

echo "Community Compose fresh-install smoke test passed."
