# dreamingtree-dl

A self-hosted web app that collects taped Dave Matthews Band shows from [dreamingtree.org](https://dreamingtree.org) and files them into a Plex music library.

- Browses the dreamingtree.org catalogue and queues shows you pick, through a local web page.
- Downloads each show's torrent with qBittorrent and keeps seeding it.
- Tags the tracks from the show's info file, builds the album name (`2025-07-12: Noblesville, IN`), and embeds cover art (from the torrent, or the show's poster on dmbalmanac.com).
- Converts SHN/APE/WAV to FLAC and checks every FLAC with `flac -t` before publishing.
- Copies the tagged result into your music library, seeds for 90 days, then removes the torrent.
- Optionally makes Plex deep-analyse the new tracks, so FLACs play straight away.
- Flags dead downloads as stalled and gives up on them, so they never block other shows.

You need your own dreamingtree.org account. Respect the tracker's rules: keep seeding (the default is 90 days), and use an unmodified, allowed client.

## What you need

- A Docker host (Docker Compose v2).
- An NFS export for downloads and work copies, writable by the uid the app runs as (`APP_UID`, 997 by default).
- An NFS export of your music library, writable by the same uid. Often that's the `plex` user's uid.
- Optional: SSH access to the Plex server for deep analysis ([docs/OPERATIONS.md](docs/OPERATIONS.md#plex-deep-analysis-post_publish_command)).

| What | Where |
|---|---|
| Web UI | `http://<docker-host>:8092` (no login: keep it on your home network) |
| qBittorrent WebUI | `http://127.0.0.1:8093` on the Docker host only |
| Config and state | `data/` in the repo (not committed) |

### Demo mode

`DTDL_FAKE=1` runs the same UI against in-memory sample data. It makes no network calls and touches no files. The compose file sets `DTDL_FAKE=0` for the real app.

```sh
docker build -t dreamingtree-dl:latest -f docker/Dockerfile .
docker run -d --rm --name dtdl-demo -p 127.0.0.1:8190:8092 -e DTDL_FAKE=1 dreamingtree-dl:latest
# open http://127.0.0.1:8190
docker stop dtdl-demo
```

## Quick start

1. Describe your network: `cp docker/.env.example docker/.env`, then set the NFS addresses and exports, the uid/gid and your own group.
2. Add your secrets: `mkdir -p data && cp config.example.json data/config.json`, then set `dreamingtree.username`, `dreamingtree.password` and `qbittorrent.password` (the qBittorrent WebUI password you'll use). Then `chmod 660 data/config.json`. `data/` is git-ignored.
3. Run the pre-deploy checks: `scripts/preflight.sh`. They check the NFS volumes, write permissions, the config, and the qBittorrent login.
4. Build and start: `scripts/deploy.sh`. It runs the preflight again, builds the image, starts `dtdl-app` and waits for its healthcheck.

Then open `http://<docker-host>:8092`. Day-to-day operation is in [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Tour of the UI

The page has a header, then three cards: Browse, Jobs and Settings.

### Header

- **dreamingtree**: `ok` or `failed`, with the detail text. **Re-login** forces a fresh login.
- **qBittorrent**: `connected` (with its version) or `down`.
- **Staging free**: free space on the nas staging share.
- **Library**: number of show folders indexed on media-server. The tooltip gives the scan time. **Rescan** re-reads the library.

### Browse

- **Filters**: Search (name or date, e.g. `2025-07-12`; a search also runs a live query on dreamingtree), Artist, Year, Source (taper, sbd, siriusxm, fm, webcast, matrix, tv), and Library (Any, Not in library, In library).
- **Hide 0 seeders** is on by default. Turn it off to see dead torrents. When shown, they sort to the bottom and their seeder count is red.
- **Seeders** is the swarm's seed count from the tracker. An unknown count is shown blank.
- **In library** shows whether you already have the show, at three levels (hover a badge for the folder):
  - `✔ this release`: a library folder has this exact release name. Nothing to gain from downloading it again.
  - `✔ same show`: the same date from the same kind of source, in a differently named folder. Almost certainly the same recording.
  - `◐ other source`: you have the date, but from a different kind of source. This is a different recording of the same show.
- Rows already queued or in the library are greyed out, and their checkbox is disabled.
- **Queue selected**: tick rows, pick the destination (from Settings), and click it. Jobs appear in the Jobs card immediately.

### Jobs

One row per job. Columns: State, Torrent (job number, destination, created time), Progress, Album and library path, Details, Actions.

- **States** (full list in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)): `queued`, `downloading`, `stalled`, `downloaded`, `analysing`, `tagged`, `copied`, `seeding`, `purged`, `error`, `abandoned`, `cancelled`.
- **Progress** shows percent, seeds, and "no progress Nh" when a download is stalled.
- **Warnings** (for example a setlist and file count that do not match) are listed under a summary line.
- **Event log** shows the job's timestamped history. Warnings and errors are prefixed `[warn]` and `[error]`.
- **Retry** appears on `error` jobs (it resumes at the step that failed) and on `abandoned` jobs (it starts the download over).
- **Cancel** removes an unfinished download and its partial data. **Give up** does the same for a stalled job. Cancel is not offered once a job is `seeding`, `purged` or `cancelled`; for a job that finished downloading, see the caveat in [docs/OPERATIONS.md](docs/OPERATIONS.md).
- The list refreshes every 5 seconds while jobs are active, otherwise every 30 seconds.

### Settings

Saved to `data/config.json`. Changes apply to new jobs.

- dreamingtree username and password (a blank password keeps the saved one).
- Retention in days.
- `post_publish_command` (empty means off).
- Convert SHN/APE/WAV to FLAC.
- Cover sources: use the image from the torrent, and use the dmbalmanac poster.
- Destinations: label, host path, and folder templates per artist code. Field reference is in [docs/CONFIG.md](docs/CONFIG.md).

The qBittorrent login, the container paths, the library mounts, and the stall and abandon timings are not editable here. Change them in `data/config.json` and restart the app.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): containers, NFS volumes, the job state machine, data flow for one show, modules, the listing cache, the library index, background threads, and the qBittorrent settings.
- [docs/CONFIG.md](docs/CONFIG.md): every config key, secrets handling, and the destination and folder-template mapping with a worked example.
- [docs/TAGGING.md](docs/TAGGING.md): album names, source suffixes, tag mapping, cover art, track numbering, conversion, warnings, and the regression corpus.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): the runbook. Deploy, logs, preflight fixes, stalled and abandoned downloads, purge, backups, moving hosts, and troubleshooting.
- [docs/CHANGELOG.md](docs/CHANGELOG.md): version history.

## Tests

```sh
scripts/test.sh
```

The tests run in a small Docker image, so there's nothing to install on the host. Pages captured from dreamingtree.org and dmbalmanac.com aren't included: one site is a private tracker whose pages identify an account, and the other is another site's content. The scraper and poster-parsing tests skip without them. The tagging tests run against a corpus of real tape info files (`tests/fixtures/info/`), with the tapers' email addresses removed.

## Licence

MIT. See [LICENSE](LICENSE).
