#!/bin/sh
# Pre-deploy checks for dreamingtree-dl. Prints PASS/FAIL lines, exits 1 on any FAIL.
# Credentials are never printed: config.json is only read for shape and emptiness.
set -u
cd "$(dirname "$0")/.."

COMPOSE_FILE=docker/docker-compose.yml
PROJECT=dreamingtree-dl                 # compose project name (name: in the compose file)
STAGING_VOL=${PROJECT}_dtdl_staging
LIBRARY_VOL=${PROJECT}_dtdl_library
APP_UID=997
APP_GID=997
ALPINE=alpine:3.20
PY_IMAGE=python:3.11-slim              # stdlib-only helper for the config/API check
DTDL_PORT=8092
QBIT_URL=http://127.0.0.1:8093          # host-side qBittorrent WebUI
DATA_DIR="$PWD/data"
CONFIG="$DATA_DIR/config.json"

FAILS=0
CHECKS=0
pass() { CHECKS=$((CHECKS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { CHECKS=$((CHECKS + 1)); FAILS=$((FAILS + 1)); printf 'FAIL  %s\n' "$1"; }

# probe_write VOLUME_OR_HOSTPATH MOUNTPOINT SUBDIR CREATE(yes|no) LABEL
# Runs as the app uid, creates and deletes a temp file under MOUNTPOINT/SUBDIR.
probe_write() {
    vol=$1; mp=$2; sub=$3; create=$4; label=$5
    tmp=".dtdl-preflight-$$"
    if docker run --rm --user "$APP_UID:$APP_GID" --group-add 1000 -v "$vol:$mp" "$ALPINE" sh -c "
        set -e
        d=$mp/$sub
        if [ '$create' = yes ]; then mkdir -p \"\$d\"; fi
        test -d \"\$d\"
        f=\"\$d/$tmp\"
        touch \"\$f\"
        rm \"\$f\"
    " >/dev/null 2>&1; then
        pass "uid $APP_UID can create and delete a file in $label"
    else
        fail "uid $APP_UID cannot create and delete a file in $label"
    fi
}

# check_nfs_volume NAME MOUNTPOINT LABEL
# Passes only if the named volume is an NFS volume and mounts as NFS inside a container.
check_nfs_volume() {
    name=$1; mp=$2; label=$3
    if ! docker volume inspect "$name" >/dev/null 2>&1; then
        fail "$label: volume $name does not exist (create it: docker compose -f $COMPOSE_FILE create app)"
        return 1
    fi
    type=$(docker volume inspect -f '{{index .Options "type"}}' "$name")
    device=$(docker volume inspect -f '{{index .Options "device"}}' "$name")
    if [ "$type" != nfs ]; then
        fail "$label: volume $name is not an NFS volume (type=${type:-none})"
        return 1
    fi
    mounted=$(docker run --rm -v "$name:$mp" "$ALPINE" awk -v m="$mp" '$2 == m { print $3 }' /proc/mounts 2>/dev/null)
    case $mounted in
        nfs*) pass "$label mounts over NFS ($device at $mp)" ;;
        *) fail "$label: $mp is not an NFS mount inside a container (got '${mounted:-nothing}')"; return 1 ;;
    esac
    return 0
}

# --- Docker -----------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
    fail "docker daemon not reachable"
    printf '\nRESULT: FAIL (%d failing of %d checks)\n' "$FAILS" "$CHECKS"
    exit 1
fi
pass "docker daemon reachable"

if docker compose -f "$COMPOSE_FILE" config -q >/dev/null 2>&1; then
    pass "compose file validates ($COMPOSE_FILE)"
else
    fail "compose file does not validate ($COMPOSE_FILE)"
fi

if docker image inspect "$ALPINE" >/dev/null 2>&1 || docker pull -q "$ALPINE" >/dev/null 2>&1; then
    pass "helper image $ALPINE available"
else
    fail "cannot pull helper image $ALPINE"
fi

# --- NFS volumes and uid 997 write access -----------------------------------
if check_nfs_volume "$STAGING_VOL" /staging "staging"; then
    probe_write "$STAGING_VOL" /staging torrents yes "/staging/torrents"
    probe_write "$STAGING_VOL" /staging work yes "/staging/work"
fi
if check_nfs_volume "$LIBRARY_VOL" /library "library"; then
    probe_write "$LIBRARY_VOL" /library DMB_Live no "/library/DMB_Live"
fi

# --- App data directory and config.json -------------------------------------
if [ -d "$DATA_DIR" ]; then
    probe_write "$DATA_DIR" /app/data "" no "data/ (sqlite, logs)"
else
    fail "data directory $DATA_DIR does not exist"
fi

if [ -f "$CONFIG" ]; then
    mode=$(stat -c %a "$CONFIG")
    if [ "$mode" = 660 ]; then
        pass "data/config.json mode is 660 (docker-host + app group)"
    else
        fail "data/config.json mode is $mode, expected 660 (chmod 660)"
    fi
    if docker run --rm --user "$APP_UID:$APP_GID" --group-add 1000 -v "$DATA_DIR:/app/data" "$ALPINE" test -r /app/data/config.json >/dev/null 2>&1; then
        pass "data/config.json is readable by uid $APP_UID"
    else
        fail "data/config.json is not readable by uid $APP_UID (see docs/OPERATIONS.md, ownership)"
    fi
else
    fail "config.json not found at $CONFIG"
fi

# --- Config contents and qBittorrent API (python prints PASS/FAIL lines) ----
# The check runs inside a container as uid 997, as the app user (uid 997 + group 1000, like compose)
# --network host lets it reach the
# qBittorrent WebUI on 127.0.0.1.
if [ -f "$CONFIG" ]; then
    if ! docker image inspect "$PY_IMAGE" >/dev/null 2>&1; then
        docker pull -q "$PY_IMAGE" >/dev/null 2>&1 || true
    fi
    report=$(docker run --rm -i --network host --user "$APP_UID:$APP_GID" --group-add 1000 \
        -v "$DATA_DIR:/app/data:ro" "$PY_IMAGE" python -I - /app/data/config.json "$QBIT_URL" <<'PY'
import http.cookiejar, json, sys, urllib.error, urllib.parse, urllib.request
path, base = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
except OSError:
    print("FAIL  config.json cannot be read by uid 997")
    sys.exit(0)
except ValueError:
    print("FAIL  config.json does not parse as JSON")
    sys.exit(0)

dt = cfg.get("dreamingtree") or {}
if all(isinstance(dt.get(k), str) and dt[k].strip() for k in ("username", "password")):
    print("PASS  config.json parses and has dreamingtree credentials (values not shown)")
else:
    print("FAIL  config.json parses but dreamingtree username or password is empty")

qb = cfg.get("qbittorrent") or {}
data = urllib.parse.urlencode({"username": qb.get("username", ""),
                               "password": qb.get("password", "")}).encode()
try:
    # qBittorrent answers login with 200 "Ok." (or 204 when localhost auth bypass is on).
    # "Fails." or 403 means the configured login was rejected.
    # qBittorrent 5 sets its session cookie as QBT_SID_<port>, so keep a cookie jar.
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with opener.open(urllib.request.Request(base + "/api/v2/auth/login", data=data), timeout=10) as r:
        body = r.read().decode(errors="replace").strip()
        login_ok = r.status in (200, 204) and body in ("", "Ok.")
    try:
        with opener.open(base + "/api/v2/app/version", timeout=10) as r:
            version_ok = r.status == 200
    except urllib.error.HTTPError:
        version_ok = False
    if login_ok and version_ok:
        print("PASS  qBittorrent Web API answers and accepts the configured login")
    elif version_ok:
        print("FAIL  qBittorrent login rejected (HTTP %d)" % r.status)
    else:
        print("FAIL  qBittorrent Web API did not return its version")
except Exception as exc:
    print("FAIL  qBittorrent Web API not answering at %s (%s)" % (base, type(exc).__name__))
PY
    )
    if [ -z "$report" ]; then
        fail "config.json and qBittorrent API check did not run (helper image $PY_IMAGE)"
    else
        printf '%s\n' "$report"
        CHECKS=$((CHECKS + $(printf '%s\n' "$report" | grep -c '^PASS\|^FAIL')))
        FAILS=$((FAILS + $(printf '%s\n' "$report" | grep -c '^FAIL')))
    fi
fi

# --- Port 8092 --------------------------------------------------------------
if ss -ltn 2>/dev/null | awk '{ print $4 }' | grep -q ":$DTDL_PORT\$"; then
    owner=$(docker ps --filter "publish=$DTDL_PORT" --format '{{.Names}}')
    if [ "$owner" = dtdl-app ]; then
        pass "port $DTDL_PORT is used by dtdl-app (redeploy)"
    else
        fail "port $DTDL_PORT is in use by '${owner:-a non-docker process}', not dtdl-app"
    fi
else
    pass "port $DTDL_PORT is free"
fi

# --- Summary ----------------------------------------------------------------
echo
if [ "$FAILS" -gt 0 ]; then
    printf 'RESULT: FAIL (%d failing of %d checks)\n' "$FAILS" "$CHECKS"
    exit 1
fi
printf 'RESULT: PASS (%d checks)\n' "$CHECKS"
exit 0
