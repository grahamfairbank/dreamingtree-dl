#!/bin/bash
# One-time setup on media-server, run with sudo:
#   sudo bash setup.sh "<contents of data/ssh/plex_analyze.pub from docker-host>"
#
# Installs /usr/local/bin/dtdl-plex-analyze and gives dreamingtree-dl an SSH key that can do
# exactly one thing as the plex user: run that helper. No shell, no forwarding, no other commands.
set -euo pipefail
PUBKEY="${1:?usage: sudo bash setup.sh \"ssh-ed25519 AAAA... dtdl-app@docker-host\"}"
case "$PUBKEY" in ssh-ed25519\ *|ssh-rsa\ *|ecdsa-*) ;; *) echo "that doesn't look like a public key" >&2; exit 1;; esac
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

install -m 755 -o root -g root "$HERE/dtdl-plex-analyze" /usr/local/bin/dtdl-plex-analyze

home="$(getent passwd plex | cut -d: -f6)"
install -d -m 700 -o plex -g plex "$home/.ssh"
auth="$home/.ssh/authorized_keys"
touch "$auth"
grep -v 'dtdl-plex-analyze' "$auth" > "$auth.new" || true      # replace any earlier dtdl key
echo "restrict,command=\"/usr/local/bin/dtdl-plex-analyze\" $PUBKEY" >> "$auth.new"
mv "$auth.new" "$auth"
chown plex:plex "$auth"
chmod 600 "$auth"

# sshd must let plex log in with a key (it has a shell, /usr/bin/bash). Warn about common blocks.
if sshd -T 2>/dev/null | grep -qiE '^(allowusers|allowgroups) '; then
    echo "NOTE: sshd has AllowUsers/AllowGroups set; make sure 'plex' is allowed:"
    sshd -T | grep -iE '^(allowusers|allowgroups) '
fi
echo "done: dreamingtree-dl can now run dtdl-plex-analyze as plex"
echo "test from docker-host: ~/dreamingtree-dl/scripts/plex-analyze.sh status /srv/music/DMB_Live"
