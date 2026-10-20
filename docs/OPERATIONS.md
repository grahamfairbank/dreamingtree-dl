# Operations runbook

Everything here runs on docker-host, from the repo root `~/dreamingtree-dl`, unless it says otherwise. The app's background behaviour is in [ARCHITECTURE.md](ARCHITECTURE.md). The settings are in [CONFIG.md](CONFIG.md).

## Key facts

| What | Where |
|---|---|
| Web UI | http://docker-host.lan:8092 |
| Health | `curl -s http://127.0.0.1:8092/healthz` returns `{"ok":true}` |
| qBittorrent WebUI | http://127.0.0.1:8093 (on docker-host only) |
| Containers | `dtdl-app`, `dtdl-qbittorrent` |
| Compose file | `docker/docker-compose.yml` (run `docker compose -f docker/docker-compose.yml ...` from the repo root) |
| Config and state | `data/` (see [ARCHITECTURE.md](ARCHITECTURE.md#the-data-folder)) |
| Torrent data | `nas:/export/dreamingtree-dl/torrents/` (mounted as `/staging/torrents`) |
| Work copies | `nas:/export/dreamingtree-dl/work/` (mounted as `/staging/work`), removed after publish |
| Library | `media-server:/srv/music/DMB_Live/<torrent name>/` |
| Owners | Your files on docker-host belong to your user (uid and gid 1000 here). uid 997 on docker-host is the system user `systemd-timesync`. The containers run as uid 997, which is why some files in `data/` show that owner. Do not chown them. |

## Deploy and upgrade

### First deploy

1. Fill `data/config.json` (see the quick start in the [README](../README.md#quick-start)).
2. `scripts/preflight.sh`. It must end with `RESULT: PASS`.
3. `scripts/deploy.sh`. It runs preflight again, builds the image, starts `dtdl-app` only, and waits up to 150 seconds for the healthcheck to report healthy.

### Upgrade after a code change

1. Run the tests: `scripts/test.sh`. The last run passed 251 tests, with 2 skipped.
2. Commit the change if you want it in history. There is no git remote; commits stay local.
3. `scripts/deploy.sh`. The image is rebuilt from `app/` and `web/`, so the code change needs a rebuild, not just a restart.

`deploy.sh` does not touch `dtdl-qbittorrent`, so downloads keep going during a deploy.

### Config-only change

Use the Settings page where it offers the key. It takes effect at once. For anything else, edit `data/config.json`, keep it at mode 660, then restart:

```sh
docker restart dtdl-app
```

Restarting `dtdl-app` does not stop downloads. qBittorrent keeps them going, and the app picks the jobs up from the database.

### Restart qBittorrent

Only when needed, for example after a qBittorrent upgrade. Its torrents resume when it starts, but check the Jobs page afterwards.

```sh
docker restart dtdl-qbittorrent
docker restart dtdl-app   # the app sets qBittorrent's preferences at start, so restart it once qBittorrent is up
```

## Logs

```sh
docker logs --tail 200 dtdl-app          # last 200 lines
docker logs -f dtdl-app                  # follow
docker logs dtdl-app 2>&1 | grep -iE 'warn|error|exception|failed' | tail -30
docker logs --tail 100 dtdl-qbittorrent
```

The app logs to stdout only. There are no log files in `data/`. Uvicorn writes an access line for each request, so the log is busy: grep rather than read it.

Lines worth knowing:

| Log text | Meaning |
|---|---|
| `library scan: N show folders` | The library was rescanned. |
| `listing sync: N rows (full\|recent)` | The listing cache was refreshed. |
| `full crawl: ...` | The first crawl or a manual re-crawl finished. The line gives rows fetched and cached, and the site's total. |
| `year YYYY has N torrents, over the site's 1000 cap` | Some torrents in that year cannot be reached. Expect this only for very busy years. |
| `listing sync failed: ...` | The site could not be reached or login failed. The app waits 6 hours before trying again. Restart to retry sooner. |
| `could not set qBittorrent preferences` | qBittorrent was not up at start. Restart the app once qBittorrent is up. |
| `advance failed for job N` | A job raised outside its normal error handling. Check that job's event log. |
| `purge failed for job N` | A purge could not remove a torrent. Retry happens at the next daily check. |
| `library scan: cannot list <folder>` | A media-server folder returned an I/O error and was skipped. |

## Preflight failures

`scripts/preflight.sh` prints PASS and FAIL lines. It never prints a credential. Fix each FAIL, then run it again. Each check, and its fix:

| Check | Failure | Fix |
|---|---|---|
| docker daemon reachable | `docker info` fails | `systemctl status docker`. docker-host is in the `docker` group. |
| compose file validates | `docker compose ... config` fails | Run that command by hand to see the error. |
| helper image available | `alpine:3.20` cannot be pulled | Check network access to Docker Hub. |
| volume exists | `dreamingtree-dl_dtdl_staging` or `..._dtdl_library` missing | `docker compose -f docker/docker-compose.yml create app` |
| volume is NFS | Volume type is not `nfs` | The volume was created with other options. Stop the app (`docker stop dtdl-app`), remove the volume (`docker volume rm dreamingtree-dl_dtdl_staging`, or `..._dtdl_library`), then run the `create app` command above. The volume holds no data of its own. |
| mounts over NFS inside a container | The mount point is not NFS | The server is down or not exported. Check the server: `exportfs -v` on nas or media-server, and that docker-host can reach `<nas-ip>` or `<media-server-ip>`. A soft mount that fails shows up here. |
| uid 997 can write `/staging/torrents` and `/staging/work` | Permission denied | On nas (root is not squashed there): `sudo chown -R 997:997 /export/dreamingtree-dl`, from nas. |
| uid 997 can write `/library/DMB_Live` | Folder missing or not writable | The folder must already exist on media-server. media-server squashes root, so never chown as root from docker-host. Ask the media-server owner to make `DMB_Live` writable by 997:997. Verified working on 2026-10-20. |
| uid 997 can write `data/` | Permission denied | `data/` must be `drwxrwsr-x` owned by `docker-host:docker-host`. Fix with `chgrp docker-host data && chmod g+rws data`. |
| `config.json` exists | Missing | `cp config.example.json data/config.json`, then fill it in. |
| `config.json` mode is 660 | Other mode | `chmod 660 data/config.json` |
| `config.json` readable by uid 997 | Not readable | `chgrp docker-host data/config.json`, then `chmod 660`. |
| `config.json` parses and has dreamingtree credentials | Empty username or password | Fill them in with the Settings page or by editing the file, then restart. |
| qBittorrent Web API answers and accepts the login | Not answering, or login rejected | `docker ps --filter name=dtdl-qbittorrent`, then `docker restart dtdl-qbittorrent`. If the login is rejected, the password in `qbittorrent.password` does not match the WebUI. Set one and make them match. |
| port 8092 free or used by dtdl-app | Another process holds it | Find it with `ss -ltnp | grep 8092`, and stop it. |

## Using the qBittorrent WebUI

Open http://127.0.0.1:8093 on docker-host. From another machine, forward it first: `ssh -L 8093:127.0.0.1:8093 docker-host`, then open http://127.0.0.1:8093 on that machine.

Log in as `admin`. The password is `qbittorrent.password` in `data/config.json`. Copy it from there rather than printing the file.

The app's torrents are in category `dreamingtree-dl`. In the torrent details:

- **Seeds and peers** show whether anyone is reachable. Zero seeds with no progress means nothing can be downloaded right now.
- **Trackers** shows each tracker's last message. "Not working" with an error, or a seed count with no connections, points to the tracker or the seeders.
- **Connection status** shows whether qBittorrent is reachable for incoming peers, and it should read connected.

Do not change files in a torrent folder, and do not move torrents to other paths. The app and the seeding depend on them.

## Stalled and abandoned downloads

A download that stops making progress is never fatal to the others. Here is what you see and what to do.

**Stalled** (after `stall_after_hours`, 24 by default):

- Jobs shows `stalled` in the pending colour. The progress line shows the percentage, the seed count, and "no progress Nh".
- The app keeps polling. If progress moves again, the row goes back to `downloading` by itself, and the event log says "download resumed".
- Choices:
  - **Wait.** Nothing else is needed. Stalled torrents are polled, so they recover automatically.
  - **Give up** (the button on a stalled row). Removes the torrent and partial data, and the job becomes `cancelled`. You can queue the show again from Browse later.

**Abandoned** (after `abandon_after_days`, 14 by default, measured from the last progress):

- The app removes the torrent and its partial data itself. The row shows `abandoned`, with the seed count at the time in its error text.
- **Retry** queues the job again and downloads the `.torrent` afresh. The download starts over.
- If the show is still dead, the Browse table shows its seeders. Look for a seed count of 0 (red) before queueing a show.

Set `abandon_after_days` to `0` to never give up. The row then stays stalled indefinitely.

### Why old SHN torrents often fail

Old shows are often offered as SHN (Shorten) releases. Their trackers still list 3 or 4 seeders, but those seeders are offline, or not reachable from the internet. The tracker keeps listing them, and qBittorrent connects to none. Four torrents from the sample set (`dm2004-01-09.tv`, `dm1999-06-15.Q101.5.shnf`, `dm1997-01-24.fm.shnf`, `dmb1997-06-13.akg461`) sat at 0% for hours with 3 or 4 seeders listed. qBittorrent itself was connected. Those torrents were given up.

The one that did complete (`dmb2002-12-18.conan`) sat at 0% until its seeders came online. So waiting sometimes works.

Practical rules:

- Check the seed count in Browse before queueing an old SHN release. A red 0 means it will stall.
- Prefer a flac16 or sbd release of the same show if one has seeders.
- A stalled SHN download with seeds listed but none connecting is normal. Wait the 24 hours, then decide.
- Torrent folder names can differ from the site's release name. For example, `dmb1996-09-06.newsource.shnf` was listed as `dmb1996-09-06.ecm909a.shnf`. The album date then comes from the release name, not the info file.

## Retention purge

- Each finished download has a `completed_at` time. After `retention_days` (90 by default) the purge removes the torrent from qBittorrent, with its files, and removes the staging copy. The job becomes `purged`.
- The purge check runs at app start, and then once an hour. It acts at most once per UTC day, remembered in the `meta` table as `last_purge_date`.
- The library copy on media-server is never touched by the purge.
- To see purges: `docker logs dtdl-app 2>&1 | grep -i purge`. Each purged job has the event "purged (torrent and data removed)".

To purge sooner, lower `retention_days` in Settings. The next daily check then removes every completed torrent older than the new value. Put the value back afterwards. Only do this if the ratio rules allow it (see [Ratio and seeding](#ratio-and-seeding)).

## Rescan the library

Either press **Rescan** in the header, or:

```sh
curl -s -X POST http://docker-host.lan:8092/api/library/rescan
```

The reply gives the show count and the scan time. The header then shows the count. The scan also runs at start and every 6 hours. A scan takes about 4 seconds for the current library.

If the count is 0 or "unknown", the library mount is missing. Run preflight, and check `docker exec dtdl-app ls /library`.

## Full re-crawl of the catalogue

The first start crawls the whole catalogue, year by year, and sets `full_crawl_done` in the `meta` table. A later full crawl needs that key removed. Use this after the site has changed its catalogue a lot, or if the cache looks incomplete.

1. Remove the key, using a throwaway container that runs as the app user:

   ```sh
   docker run --rm --user 997:997 --group-add 1000 -v "$PWD/data:/app/data" \
     dtdl-dev python -c "import sqlite3; c = sqlite3.connect('/app/data/dtdl.sqlite3', timeout=30); c.execute(\"DELETE FROM meta WHERE key='full_crawl_done'\"); c.commit()"
   ```

2. Restart the app, because the sync thread only checks the key when it starts or after its 6-hour wait:

   ```sh
   docker restart dtdl-app
   ```

The crawl makes one request a second, so it takes a while. Watch `docker logs dtdl-app` for `full crawl: ...`. The Browse table keeps working during the crawl, with the rows cached so far.

## Backups

Back up `data/`. The job history and settings exist only there. The torrent data on nas and the library files on media-server can usually be fetched again from the site, but a re-download takes time.

What is in `data/`, and what matters:

| Path | Back up? | Why |
|---|---|---|
| `config.json` | Yes, carefully | Settings and both passwords. Store the backup with the same care as the original. |
| `dtdl.sqlite3` | Yes | Job history, event logs, listing cache, and the full-crawl flag. |
| `qbittorrent/` | Yes | qBittorrent's config and torrent backups. Without `BT_backup/` the torrents are forgotten. |
| `library_index.json` | No | Rebuilt by the scan. |
| `dreamingtree.cookies` | No | Session cookies. The app logs in again. Pier cannot read this file anyway. |
| `cache/` | No | dmbalmanac cache. Refetched on demand. |

Pier cannot read `dreamingtree.cookies` or the qBittorrent logs and socket, which are owned by uid 997. Exclude them from the backup:

```sh
mkdir -p "$HOME/dtdl-backup"
tar -czf "$HOME/dtdl-backup/data-$(date +%F).tar.gz" \
  --exclude='data/dreamingtree.cookies' \
  --exclude='data/qbittorrent/qBittorrent/ipc-socket' \
  --exclude='data/qbittorrent/qBittorrent/logs' \
  data
```

The SQLite files are only consistent when the app is not writing. Either stop the app while you copy (`docker stop dtdl-app`, copy, `docker start dtdl-app`), or accept a backup that may need a moment's recovery. Downloads continue while the app is stopped.

Keep the archive off docker-host as well, and do not leave it in a shared folder: it contains both passwords.

## Moving to a new host

1. Install Docker and the same compose project layout. Copy the repo.
2. Copy `data/` (see Backups). Keep its permissions: `config.json` at mode 660.
3. Create the NFS exports on nas and media-server for the new host's address, and make sure nas's `/export/dreamingtree-dl` is owned by 997:997. Update the `addr=` values in `docker/docker-compose.yml` if the NFS server addresses change.
4. The app container adds group 1000 (`group_add` in the compose file). On the new host, make `data/` group-writable and setgid for the author's group, as on docker-host. If that group is not gid 1000, change `group_add` to match.
5. Update `qbittorrent.url` and the port forward (if the peer port changes).
6. `scripts/preflight.sh`, then `scripts/deploy.sh`.
7. The qBittorrent WebUI state comes with `data/qbittorrent/`. Check the torrent list after the first start. A torrent whose data is missing shows `missingFiles`. Recheck it, or give up and re-queue.

## Ratio and seeding

Facts from the tracker, as checked on 2026-10-20:

- The tracker has a required ratio, which depends on how much you have downloaded and how much you share. It rises to 0.60 above 100 GB. Your profile page shows the current required ratio. At the last check it was 0.00, and the account ratio was 0.25.
- The client whitelist is empty. The app does not need a particular client, and it must never claim to be one. The app identifies itself to the site as `dreamingtree-dl/0.1`.
- There is no VPN.

What the app does about it:

- It seeds every completed torrent for `retention_days` (90 by default). It never stops a torrent by ratio or by seeding time. That is what `max_ratio_enabled` and `max_seeding_time_enabled` are set to `false` for.
- Do not lower `retention_days` below what the ratio rules need. Check the profile first.
- Port 6881 (TCP and UDP) is published to the host. Forwarding it on the router to docker-host helps peers reach you, which helps seeding.

## Plex deep analysis (post_publish_command)

**Why:** Plex can't stream a newly added FLAC until `Plex Media Scanner --analyze-deeply` has run on it. Until then, playback fails with "the connection to Plex was lost". The sign in Plex's database is `media_items.bitrate` being empty. Copying files into the library makes Plex create the items, but it doesn't deep-analyse them.

**What happens now:** after each show is published, the app runs `post_publish_command = /app/scripts/plex-analyze.sh` **in the background**. This SSHes to media-server as `plex` with a key that can run only `/usr/local/bin/dtdl-plex-analyze`. That helper:
1. asks Plex to scan the show's folder (API on 127.0.0.1:32400, using the token from Preferences.xml);
2. waits, for up to 15 minutes, until Plex has one item per audio file;
3. runs `Plex Media Scanner --analyze-deeply --item <id>` for exactly those item IDs that lack a bitrate, found by file path in Plex's database, so no ID ranges are involved;
4. verifies every track now has a bitrate.

The job's event log shows the result, e.g. `post_publish_command exit 0: OK folder=… tracks=23 deep_analysed=23 missing_bitrate=0 items=54800-54822 secs=14`. Exit 2 means some tracks still lack a bitrate, and exit 3 means Plex never picked the folder up. **An `INCOMPLETE` (exit 2) result isn't automatically a problem:** on 2026-10-20, two old tracks (`cy2008/dmb2008-08-19.siriusxm.web-dl.flac16` t22 and t23) passed `flac -t` and played fine on Sonos, yet Plex wouldn't store a bitrate for them however often they were deep-analysed. So if you see INCOMPLETE, try playing the named tracks before doing anything. Only if they fail, use Plex's Analyze on the album, or remove and re-add the folder. A hook failure never fails the job. The limit is `post_publish_timeout_minutes` (120).

**By hand** (from docker-host):

```bash
docker exec dtdl-app /app/scripts/plex-analyze.sh status "/srv/music/DMB_Live/<folder>"
docker exec dtdl-app /app/scripts/plex-analyze.sh "/srv/music/DMB_Live/<folder>"
docker exec dtdl-app /app/scripts/plex-analyze.sh sweep
```

`status` is read-only. The second command deep-analyses one folder. `sweep` deep-analyses every music track in Plex that lacks a bitrate, which also catches music you add yourself. The helper logs to `/var/lib/plexmediaserver/dtdl-plex-analyze.log` on media-server.

**Setup and updates** (one time, and again whenever `media-server/dtdl-plex-analyze` changes). From docker-host:

```bash
scp ~/dreamingtree-dl/media-server/dtdl-plex-analyze ~/dreamingtree-dl/media-server/setup.sh media-server:dtdl-plex/
ssh -t media-server 'sudo bash ~/dtdl-plex/setup.sh "$(cat ~/dtdl-plex/plex_analyze.pub 2>/dev/null || echo PASTE-PUBKEY)"'
```

The public key is `data/ssh/plex_analyze.pub`. Its directory is readable only by the app user, so to see it: `docker exec dtdl-app cat /app/data/ssh/plex_analyze.pub`. setup.sh installs the helper, owned by root, and puts a `restrict,command="/usr/local/bin/dtdl-plex-analyze"` line in `/var/lib/plexmediaserver/.ssh/authorized_keys`. The key can't open a shell or reach anything outside `/srv/music`.

**Integrity check:** before publishing, every FLAC is decoded with `flac -t` (`verify_flac`, on by default). A corrupt or truncated download stops in `error` with the file names, and nothing reaches the library.

**Using another command:** `post_publish_command` runs with `/bin/sh -c` in the container as uid 997. `$1` is the container path (`/library/...`), and `DTDL_HOST_PATH` is the library-side path (`/srv/music/...`).

## Known gaps

Things that behave differently from what you might assume. None of these loses data, but two need a manual step.

1. **Cancelling a job after its download finished leaves the torrent seeding.** Cancel removes the torrent only if the download had not completed. A cancelled job that finished downloading keeps its torrent in qBittorrent, and the purge does not look at cancelled jobs. Remove it by hand: in the qBittorrent WebUI, delete the torrent with its files, and check that the staging folder is gone.
2. **A failed listing sync waits six hours.** If the site is down at start, or login fails, the browse cache is not refreshed until the next 6-hour wait. Restart the app to retry sooner. The Re-login button does not refresh the cache.
3. **Retry does not reach cancelled jobs.** The Jobs page offers Retry only for `error` and `abandoned`. A cancelled show is queued again from Browse.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Header: dreamingtree `failed`, detail "login failed: ..." | Wrong username or password, or the site is down | Fix the password on Settings, Save, then Re-login. If the site is down, wait. |
| Browse says "Listing failed" | The app returned an error for the listing request | `docker logs --tail 100 dtdl-app`. Restart the app if the error persists. |
| Browse is empty after a restart | First crawl still running, or the sync failed | Wait for `full crawl: ...` in the log. If the sync failed, restart. |
| Header: qBittorrent `down` | The container is stopped, or the password is wrong | `docker ps --filter name=dtdl-qbittorrent`. `docker restart dtdl-qbittorrent`. Check `qbittorrent.password`. |
| Header: Library `unknown` or 0 shows | `/library` is not mounted or not readable | Run preflight. `docker exec dtdl-app ls /library`. |
| Job `error`, "[downloading] qBittorrent reports state error" | qBittorrent has a problem with the torrent | Open the torrent in the WebUI and recheck it. Retry, or Cancel. |
| Job `error`, "[downloading] qBittorrent reports state missingFiles" | The torrent's files were moved or deleted on nas | Cancel, then queue it again. Do not move files by hand. |
| Job `error`, "[downloaded] no info .txt file found" | The torrent has no info file, so there is nothing to tag | Cancel. Queue another release of the show if one exists. |
| Job `error`, "[downloaded] could not determine the show date" | No usable date in the name or the info file | Cancel, or queue another release. |
| Job `error`, "[analysing] ffmpeg failed on ..." | The audio is corrupt or unsupported | Read the event log for the ffmpeg message. Retry after checking the source. |
| Job `error`, "[tagged] destination already exists and is not empty" | A folder with that name is already in the library | Compare the two. If the existing folder is a stale copy you want replaced, move it aside on media-server over ssh (`ssh media-server`, as your own user, never as root), then Retry. Otherwise Cancel. |
| Job `error`, "[tagged] ... is not under any library_mounts prefix" | The destination path has no mount mapping | Add the prefix to `library_mounts` in `data/config.json`, restart, then Retry. |
| Job `error`, "[tagged] no folder_template for ... and no 'default'" | A destination's template has no fallback | Add a `default` key in Settings, then Retry. |
| Job `error`, "[tagged] unsafe folder template result" | A template produced a path with `..` or an absolute path | Fix the template. |
| Job `stalled` for days | No seeders are connectable | See [Stalled and abandoned downloads](#stalled-and-abandoned-downloads). |
| Job has warnings about the setlist and file count | The info file's setlist does not match the audio | Check the titles in the library folder against the info file. The titles were left as they were. |
| "post_publish_command exit N" warning | The hook failed | Read the output in the event log. Run the command by hand inside the container: `docker exec dtdl-app /bin/sh -c '<command>' test /library/<folder>`. |
| Settings: "Save failed: ..." | Validation error or write failure | Read the message. If it mentions a file, check `data/config.json` ownership and mode. |
| Health: `unhealthy` after deploy | The app did not start | `docker logs --tail 50 dtdl-app`. Fix the error, then `scripts/deploy.sh` again. |
| Library scan logs `cannot list` | A media-server folder returned an I/O error | The mount is soft. Check media-server is up. The folder is skipped until the next scan. |
| Jobs show "work directory is missing; redoing analysis and tagging" | The work copy was removed before publish | Automatic. The job goes back and redoes analysis and tagging. |
| Jobs stop moving | The scheduler thread is failing, or the app is wedged | `docker logs --tail 100 dtdl-app \| grep -i tick`. Restart with `docker restart dtdl-app`. Jobs resume from the database. |
