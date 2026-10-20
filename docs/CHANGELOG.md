# Changelog

## 0.1.0 (2026-10-20)

First working version, running on docker-host as `dtdl-app` at http://docker-host.lan:8092, with qBittorrent in `dtdl-qbittorrent`.

Built from five commits on 2026-10-20: `b872e1b`, `e73771e`, `73d5825`, `5d159a3`, `1300669`.

### Added

- **Web UI**: Browse, Jobs and Settings pages, styled after Nugs Downloader. No login.
- **Browse**: the dreamingtree catalogue as a cached table, with filters for artist, year, source and library status, a "Hide 0 seeders" toggle, and multi-select queueing. Text search also runs a live site search.
- **Listing cache**: `seen_torrents` table, refreshed every 6 hours. The first start crawls the whole catalogue one year at a time, because the site caps any search at 1,000 results.
- **In library column**: matches each release against the media-server library at three levels: this release, same show, and other source. Rescans at start, every 6 hours, and on demand.
- **Jobs**: state machine from `queued` through `seeding` to `purged`, with `stalled` and `abandoned` for dead downloads, `error` with Retry, and Cancel or Give up. Per-job event log and warnings.
- **Stall handling**: a download with no progress for 24 hours is marked stalled and keeps being polled. After 14 days it is abandoned and its partial data is removed. Dead torrents do not hold qBittorrent's download slots.
- **Retention**: torrents are seeded for 90 days after download, then removed from qBittorrent and staging. The library copy is kept.
- **Metadata**: release-name parser; info-file parser for venue, city, region, country, date, lineage and setlist, tolerant of month-name typos such as "Spetember"; source suffixes `siriusxm`, `sbd`, `fm`, `webcast` (including YouTube and webrip), `matrix` and `tv` (TV broadcasts, added on 2026-10-20); album names in the form `YYYY-MM-DD: City, ST`, with UK, country and venue-fallback rules.
- **Tags and cover**: FLAC tags per the tag mapping, including setlist track numbers and discs. Cover art from the torrent folder, else the dmbalmanac poster (cached), else none. SHN, APE and WAV converted to FLAC with ffmpeg.
- **Settings**: dreamingtree login, retention, the conversion and cover toggles, the post-publish hook, and destinations with folder templates.
- **Docker and scripts**: compose project with the app and qBittorrent, NFS volumes for nas staging and media-server library, `scripts/preflight.sh` (pre-deploy checks), `scripts/deploy.sh`, and `scripts/test.sh`.
- **Demo mode**: `DTDL_FAKE=1` serves the UI from in-memory sample data.
- **Tests**: 251 passing, 2 skipped (`scripts/test.sh`).
- **Regression corpus**: 275 hand-tagged shows from the author's library. 211 match exactly (76.7%). 250 match once accepted author conventions are counted (90.9%).

### Known gaps

Listed in [OPERATIONS.md](OPERATIONS.md#known-gaps). In short: a cancelled job whose download had finished leaves its torrent seeding, a failed listing sync waits six hours before it retries, and a cancelled job cannot be retried.
