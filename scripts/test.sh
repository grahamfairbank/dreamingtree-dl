#!/bin/sh
# Run the test suite inside the dev image: scripts/test.sh [pytest args]
set -e
cd "$(dirname "$0")/.."
docker image inspect dtdl-dev >/dev/null 2>&1 || docker build -q -t dtdl-dev -f docker/Dockerfile.dev . >/dev/null
exec docker run --rm -v "$PWD:/app" -w /app dtdl-dev python -m pytest -q "$@"
