#!/usr/bin/env bash
# Build and deploy the tip of DEPLOY_BRANCH (default: main) to docker.
# Safe to call often: no-op when that commit is already deployed.
# Usage: deploy/deploy.sh [--force]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-main}"
STATE="${HOMEFLIX_DEPLOY_STATE:-$HOME/.local/state/homeflix-deploy}"
LOG="$STATE/deploy.log"
mkdir -p "$STATE"

exec 9>"$STATE/lock"
flock -n 9 || { echo "deploy already running" >&2; exit 0; }

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

SHA="$(git -C "$REPO" rev-parse --verify "refs/heads/$BRANCH")"
DEPLOYED="$(cat "$STATE/deployed_sha" 2>/dev/null || true)"
if [ "$SHA" = "$DEPLOYED" ] && [ "${1:-}" != "--force" ]; then
  echo "up to date ($SHA)"; exit 0
fi

# Build from the committed tree, not the working dir (it may be on another branch)
SRC="$STATE/src"
rm -rf "$SRC"; mkdir -p "$SRC"
git -C "$REPO" archive "$SHA" | tar -x -C "$SRC"

log "deploy $BRANCH@${SHA:0:7} (was ${DEPLOYED:0:7})"
cd "$SRC/deploy"
if ! docker compose build >>"$LOG" 2>&1; then
  log "BUILD FAILED, running container untouched"; exit 1
fi
docker compose up -d --remove-orphans >>"$LOG" 2>&1

for _ in $(seq 1 30); do
  if docker exec homeflix curl -fsS -o /dev/null http://localhost:6969/; then
    echo "$SHA" > "$STATE/deployed_sha"
    docker image prune -f >/dev/null 2>&1 || true
    log "OK ${SHA:0:7}"; exit 0
  fi
  sleep 2
done
log "HEALTHCHECK FAILED ${SHA:0:7}; see: docker logs homeflix"
exit 1
