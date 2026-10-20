#!/bin/sh
# post_publish_command for Plex: ask media-server to deep-analyse a newly published folder.
# Usage (inside dtdl-app): plex-analyze.sh <folder>        -> analyze
#                          plex-analyze.sh status <folder> -> read-only check
#                          plex-analyze.sh sweep           -> every music track without a bitrate
# The pipeline passes the container path ($1, /library/...) and the media-server path in DTDL_HOST_PATH.
# The SSH key can only run /usr/local/bin/dtdl-plex-analyze as plex (media-server/setup.sh).
set -eu
KEY="${DTDL_PLEX_KEY:-/app/data/ssh/plex_analyze}"
KNOWN="${DTDL_PLEX_KNOWN_HOSTS:-/app/data/ssh/known_hosts}"
TARGET="${DTDL_PLEX_SSH:-plex@media-server}"
case "${1:-}" in
    status|sweep) verb="$1"; shift ;;
    *) verb=analyze ;;
esac
path="${DTDL_HOST_PATH:-${1:-}}"
exec ssh -F /dev/null -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN" -o ServerAliveInterval=60 \
    "$TARGET" "$verb${path:+ $path}"
