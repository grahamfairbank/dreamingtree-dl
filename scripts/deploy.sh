#!/bin/sh
# Deploy dtdl-app: preflight, build, start the app only, wait for the healthcheck.
set -eu
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker/docker-compose.yml"

./scripts/preflight.sh

$COMPOSE build app
# --no-deps: only the app is (re)created. The running qBittorrent container is
# left alone, because a download may be in progress.
$COMPOSE up -d --no-deps app

echo "Waiting for dtdl-app healthcheck (up to 150s)..."
status=starting
i=0
while [ "$i" -lt 50 ]; do
    status=$(docker inspect -f '{{.State.Health.Status}}' dtdl-app 2>/dev/null || echo missing)
    case $status in
        healthy)
            echo "PASS  dtdl-app is healthy"
            exit 0 ;;
        unhealthy)
            echo "FAIL  dtdl-app is unhealthy; last log lines:"
            docker logs --tail 50 dtdl-app 2>&1 || true
            exit 1 ;;
    esac
    sleep 3
    i=$((i + 1))
done
echo "FAIL  dtdl-app not healthy after 150s (status: $status)"
docker logs --tail 50 dtdl-app 2>&1 || true
exit 1
